"""Event-driven backtesting engine.

Runs a Pine Script strategy against historical OHLCV data bar-by-bar:
  1. Parse the Pine Script.
  2. For each bar: evaluate the script → collect signals.
  3. Route each signal through the risk manager → paper broker.
  4. Track equity curve (cash + open position mark-to-market value).
  5. Return a BacktestResult with fills, equity curve, and metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from algotrader.backtest.metrics import PerformanceMetrics
from algotrader.broker.paper_broker import PaperBroker
from algotrader.config import RiskConfig, Settings
from algotrader.pine.evaluator import Evaluator
from algotrader.pine.lexer import Lexer
from algotrader.pine.parser import Parser
from algotrader.risk.manager import RiskManager
from algotrader.signals.models import (
    Exchange,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    SignalAction,
    TradingSignal,
)


@dataclass
class BacktestResult:
    equity_curve: List[float] = field(default_factory=list)
    fills: List[Fill] = field(default_factory=list)
    metrics: Optional[PerformanceMetrics] = None
    n_signals: int = 0


class BacktestEngine:
    """Runs a Pine Script strategy over historical bar data."""

    def __init__(
        self,
        bars: Dict[str, List[float]],
        symbol: str,
        exchange: Exchange,
        initial_capital: float = 1_000_000.0,
        risk_config: Optional[RiskConfig] = None,
    ) -> None:
        self._bars = bars
        self._symbol = symbol
        self._exchange = exchange
        self._initial_capital = initial_capital
        self._risk_config = risk_config or RiskConfig()
        self._n_bars = len(bars["close"])

    def run(self, pine_script: str) -> BacktestResult:
        """Execute the Pine Script strategy and return results."""
        # ── Parse script ──────────────────────────────────────────────────────
        tokens = Lexer(pine_script).tokenize()
        ast = Parser(tokens).parse()

        # ── Initialise broker and risk manager ────────────────────────────────
        broker = PaperBroker(initial_capital=self._initial_capital)
        risk_mgr = RiskManager(config=self._risk_config, capital=self._initial_capital)

        equity_curve: List[float] = []
        all_fills: List[Fill] = []
        n_signals = 0

        # ── Bar-by-bar simulation ─────────────────────────────────────────────
        for bar_idx in range(self._n_bars):
            bar_bars = self._bars_up_to(bar_idx)
            ev = Evaluator(
                bar_bars,
                symbol=self._symbol,
                exchange=self._exchange,
                strategy_name="backtest",
            )
            signals = ev.run(ast)

            # Only use the last signal from the last bar
            bar_signals = [s for s in signals if s.metadata.get("bar_index") == bar_idx]
            n_signals += len(bar_signals)

            for signal in bar_signals:
                self._process_signal(signal, broker, risk_mgr, bar_idx)

            # Update fills list
            all_fills = list(broker.fills)

            # Compute equity at this bar
            close_price = self._bars["close"][bar_idx]
            equity = self._compute_equity(broker, close_price)
            equity_curve.append(equity)

        # ── Compute metrics ───────────────────────────────────────────────────
        metrics = PerformanceMetrics.compute(
            equity_curve=equity_curve,
            initial_capital=self._initial_capital,
            fills=all_fills,
        )

        return BacktestResult(
            equity_curve=equity_curve,
            fills=all_fills,
            metrics=metrics,
            n_signals=n_signals,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _bars_up_to(self, bar_idx: int) -> Dict[str, List[float]]:
        """Return bars slice up to and including bar_idx."""
        n = bar_idx + 1
        return {k: v[:n] for k, v in self._bars.items()}

    def _process_signal(
        self,
        signal: TradingSignal,
        broker: PaperBroker,
        risk_mgr: RiskManager,
        bar_idx: int,
    ) -> None:
        market_price = self._bars["close"][bar_idx]

        try:
            if signal.is_exit:
                # Close existing position
                positions = broker.get_positions()
                for pos in positions:
                    if pos.symbol == signal.symbol:
                        order = Order(
                            symbol=pos.symbol,
                            exchange=pos.exchange,
                            side=OrderSide.SELL if pos.is_long else OrderSide.BUY,
                            order_type=OrderType.MARKET,
                            quantity=abs(pos.quantity),
                            strategy_name=signal.strategy_name,
                            signal_id=signal.signal_id,
                        )
                        fill = broker.submit_order(order, market_price=market_price)
                        if fill:
                            pnl = fill.value - pos.cost_basis
                            risk_mgr.record_realised_pnl(pnl)
                return

            # Entry signal
            decision = risk_mgr.evaluate(signal, broker.get_positions())
            if not decision.approved or decision.quantity <= 0:
                return

            order = Order(
                symbol=signal.symbol,
                exchange=signal.exchange,
                side=OrderSide.BUY if signal.action == SignalAction.BUY else OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=decision.quantity,
                strategy_name=signal.strategy_name,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
                signal_id=signal.signal_id,
            )
            broker.submit_order(order, market_price=market_price)

        except Exception:
            # Swallow individual signal errors in backtest (log in production)
            pass

    def _compute_equity(self, broker: PaperBroker, close_price: float) -> float:
        """Total equity = cash + mark-to-market value of open positions."""
        equity = broker.available_capital
        for pos in broker.get_positions():
            equity += abs(pos.quantity) * close_price
        return equity
