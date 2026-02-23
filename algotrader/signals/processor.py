"""Signal processing pipeline.

Applies validation and filters to raw TradingSignals before they reach
the risk manager and order engine.

Filters applied in order:
  1. Validation (symbol, price, quantity)
  2. Market hours check (optional)
  3. Deduplication (optional windowed)
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from algotrader.exceptions import (
    DuplicateSignalError,
    InvalidSignalError,
    SignalFilteredError,
)
from algotrader.signals.models import Exchange, SignalAction, TradingSignal
from algotrader.strategy.market_hours import MarketHours

# Key: (symbol, exchange, action)
_DeduKey = Tuple[str, Exchange, SignalAction]


class SignalProcessor:
    """Validates and filters trading signals."""

    def __init__(
        self,
        check_market_hours: bool = True,
        dedup_window_seconds: int = 60,
    ) -> None:
        self._check_hours = check_market_hours
        self._dedup_window = dedup_window_seconds
        self._market_hours = MarketHours()
        self._seen: Dict[_DeduKey, datetime] = {}

    def process(self, signal: TradingSignal) -> TradingSignal:
        """Run all filters. Returns the signal if valid, raises otherwise."""
        self._validate(signal)
        if self._check_hours:
            self._check_market_hours(signal)
        if self._dedup_window > 0:
            self._dedup(signal)
        return signal

    def clear(self) -> None:
        """Clear the dedup cache (e.g. at start of new trading day)."""
        self._seen.clear()

    # ── Filters ───────────────────────────────────────────────────────────────

    @staticmethod
    def _validate(signal: TradingSignal) -> None:
        if not signal.symbol:
            raise InvalidSignalError("Signal has no symbol")
        if signal.quantity is not None and signal.quantity < 0:
            raise InvalidSignalError(
                f"Negative quantity is not allowed: {signal.quantity}"
            )
        if signal.price is not None:
            if signal.price <= 0:
                raise InvalidSignalError(
                    f"Price must be positive: {signal.price}"
                )

    def _check_market_hours(self, signal: TradingSignal) -> None:
        if not self._market_hours.is_open():
            raise SignalFilteredError(
                f"Signal {signal.signal_id} filtered: market is closed"
            )

    def _dedup(self, signal: TradingSignal) -> None:
        key: _DeduKey = (signal.symbol, signal.exchange, signal.action)
        now = datetime.now(timezone.utc)

        if key in self._seen:
            last_seen = self._seen[key]
            elapsed = (now - last_seen).total_seconds()
            if elapsed < self._dedup_window:
                raise DuplicateSignalError(
                    f"Duplicate signal for {signal.symbol} {signal.action.value} "
                    f"(seen {elapsed:.1f}s ago, window={self._dedup_window}s)"
                )

        self._seen[key] = now
