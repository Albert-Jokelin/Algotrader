"""Risk manager — gates signals through exposure and loss limits.

Each call to `evaluate()` returns a RiskDecision with:
  - approved: whether to proceed
  - quantity: how many shares to trade
  - stop_loss / take_profit: computed or passed through from signal
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from algotrader.config import RiskConfig
from algotrader.exceptions import (
    DailyLossLimitExceededError,
    PortfolioExposureExceededError,
)
from algotrader.risk.position_sizer import PositionSizer, SizingMethod
from algotrader.signals.models import Position, SignalAction, TradingSignal


@dataclass
class RiskDecision:
    approved: bool
    quantity: int = 0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reason: str = ""


class RiskManager:
    """Evaluates signals against risk parameters and returns a RiskDecision."""

    def __init__(self, config: RiskConfig, capital: float) -> None:
        self._cfg = config
        self._capital = capital
        self._daily_pnl: float = 0.0
        self._sizer = PositionSizer(
            method=SizingMethod.RISK_BASED,
            risk_pct=config.max_position_pct / 2,
            default_stop_loss_pct=config.default_stop_loss_pct,
            pct_of_capital=config.max_position_pct,
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def evaluate(
        self,
        signal: TradingSignal,
        positions: List[Position],
    ) -> RiskDecision:
        """Gate the signal through all risk checks. Returns a RiskDecision."""
        self._check_daily_loss()
        self._check_portfolio_exposure(signal, positions)

        price = signal.price or 0.0
        quantity = self._compute_quantity(signal, price)

        # Clamp quantity so position doesn't exceed per-instrument limit
        max_budget = self._capital * self._cfg.max_position_pct
        if price > 0:
            max_qty = int(max_budget / price)
            quantity = min(quantity, max_qty)

        stop_loss = self._compute_stop_loss(signal, price)
        take_profit = signal.take_profit

        return RiskDecision(
            approved=True,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    def record_realised_pnl(self, pnl: float) -> None:
        """Record realised P&L (positive = profit, negative = loss)."""
        self._daily_pnl += pnl

    def reset_daily_pnl(self) -> None:
        self._daily_pnl = 0.0

    # ── Internal checks ────────────────────────────────────────────────────────

    def _check_daily_loss(self) -> None:
        max_loss = self._capital * self._cfg.max_daily_loss_pct
        if self._daily_pnl < -max_loss:
            raise DailyLossLimitExceededError(
                f"Daily loss limit exceeded: {self._daily_pnl:.2f} "
                f"(limit: -{max_loss:.2f})"
            )

    def _check_portfolio_exposure(
        self, signal: TradingSignal, positions: List[Position]
    ) -> None:
        # Only check exposure for entry signals
        if not signal.is_entry:
            return

        total_cost = sum(p.cost_basis for p in positions)
        max_exposure = self._capital * self._cfg.max_portfolio_exposure_pct
        if total_cost >= max_exposure:
            raise PortfolioExposureExceededError(
                f"Portfolio exposure limit reached: "
                f"{total_cost:.2f} >= {max_exposure:.2f}"
            )

    def _compute_quantity(self, signal: TradingSignal, price: float) -> int:
        if signal.quantity is not None:
            return signal.quantity
        if price <= 0:
            return 0
        return self._sizer.calculate(signal, self._capital, price)

    def _compute_stop_loss(self, signal: TradingSignal, price: float) -> Optional[float]:
        if signal.stop_loss is not None:
            return signal.stop_loss
        if price <= 0:
            return None
        # Default stop-loss below entry price
        if signal.action == SignalAction.BUY:
            return price * (1 - self._cfg.default_stop_loss_pct)
        # Short: stop above entry
        return price * (1 + self._cfg.default_stop_loss_pct)
