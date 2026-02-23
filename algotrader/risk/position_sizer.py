"""Position sizing algorithms.

Implements three sizing methods:
  FIXED_QUANTITY    — always trade N shares
  PERCENT_OF_CAPITAL — allocate X% of capital
  RISK_BASED         — size by risk amount and stop-loss distance
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Optional

from algotrader.signals.models import TradingSignal


class SizingMethod(str, Enum):
    FIXED_QUANTITY = "fixed_quantity"
    PERCENT_OF_CAPITAL = "percent_of_capital"
    RISK_BASED = "risk_based"


class PositionSizer:
    """Calculate how many shares to trade for a given signal."""

    def __init__(
        self,
        method: SizingMethod = SizingMethod.PERCENT_OF_CAPITAL,
        *,
        fixed_quantity: int = 1,
        pct_of_capital: float = 0.05,
        risk_pct: float = 0.01,
        default_stop_loss_pct: float = 0.02,
    ) -> None:
        self.method = method
        self.fixed_quantity = fixed_quantity
        self.pct_of_capital = pct_of_capital
        self.risk_pct = risk_pct
        self.default_stop_loss_pct = default_stop_loss_pct

    def calculate(
        self,
        signal: TradingSignal,
        capital: float,
        current_price: float,
    ) -> int:
        """Return the number of shares to trade (0 if impossible)."""
        # Signal-level override always wins
        if signal.quantity is not None:
            return signal.quantity

        if self.method == SizingMethod.FIXED_QUANTITY:
            return self.fixed_quantity

        if self.method == SizingMethod.PERCENT_OF_CAPITAL:
            return self._pct_size(capital, current_price)

        if self.method == SizingMethod.RISK_BASED:
            return self._risk_size(signal, capital, current_price)

        return 0

    # ── Sizing implementations ────────────────────────────────────────────────

    def _pct_size(self, capital: float, price: float) -> int:
        if price <= 0:
            return 0
        budget = capital * self.pct_of_capital
        return math.floor(budget / price)

    def _risk_size(
        self,
        signal: TradingSignal,
        capital: float,
        current_price: float,
    ) -> int:
        price = current_price
        stop = signal.stop_loss

        if stop is None:
            # Use default stop-loss percentage
            stop = price * (1 - self.default_stop_loss_pct)

        risk_per_share = abs(price - stop)
        if risk_per_share <= 0:
            return 0

        risk_amount = capital * self.risk_pct
        return math.floor(risk_amount / risk_per_share)
