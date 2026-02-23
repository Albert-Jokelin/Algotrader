"""TDD tests for signal processor.

The processor is a filter pipeline applied to raw signals from Pine Script.
Filters: market hours, duplicate detection, basic validation.
Written BEFORE the implementation.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from algotrader.signals.models import (
    Exchange, SignalAction, SignalSource, TradingSignal
)
from algotrader.signals.processor import SignalProcessor
from algotrader.exceptions import (
    InvalidSignalError, DuplicateSignalError, SignalFilteredError
)


def _make_signal(action=SignalAction.BUY, symbol="RELIANCE", **kw):
    return TradingSignal(
        symbol=symbol,
        exchange=Exchange.NSE,
        action=action,
        strategy_name="test",
        source=SignalSource.PINE_SCRIPT,
        **kw,
    )


class TestSignalValidation:
    def test_valid_signal_passes(self):
        proc = SignalProcessor(check_market_hours=False)
        sig = _make_signal()
        result = proc.process(sig)
        assert result is sig

    def test_missing_symbol_raises(self):
        proc = SignalProcessor(check_market_hours=False)
        with pytest.raises(InvalidSignalError):
            proc.process(_make_signal(symbol=""))

    def test_negative_quantity_raises(self):
        proc = SignalProcessor(check_market_hours=False)
        with pytest.raises(InvalidSignalError):
            proc.process(_make_signal(quantity=-1))

    def test_negative_price_raises(self):
        proc = SignalProcessor(check_market_hours=False)
        with pytest.raises(InvalidSignalError):
            proc.process(_make_signal(price=-100.0))

    def test_zero_price_raises(self):
        proc = SignalProcessor(check_market_hours=False)
        with pytest.raises(InvalidSignalError):
            proc.process(_make_signal(price=0.0))

    def test_valid_price_passes(self):
        proc = SignalProcessor(check_market_hours=False)
        sig = proc.process(_make_signal(price=2850.0))
        assert sig.price == pytest.approx(2850.0)


class TestDuplicateDetection:
    def test_duplicate_signal_raises(self):
        proc = SignalProcessor(check_market_hours=False, dedup_window_seconds=60)
        sig1 = _make_signal()
        sig2 = _make_signal()  # Same symbol + action, different signal_id
        proc.process(sig1)
        with pytest.raises(DuplicateSignalError):
            proc.process(sig2)

    def test_different_action_not_duplicate(self):
        proc = SignalProcessor(check_market_hours=False, dedup_window_seconds=60)
        proc.process(_make_signal(action=SignalAction.BUY))
        # EXIT is different — should pass
        result = proc.process(_make_signal(action=SignalAction.EXIT))
        assert result is not None

    def test_different_symbol_not_duplicate(self):
        proc = SignalProcessor(check_market_hours=False, dedup_window_seconds=60)
        proc.process(_make_signal(symbol="RELIANCE"))
        result = proc.process(_make_signal(symbol="INFY"))
        assert result is not None

    def test_dedup_expires_after_window(self):
        proc = SignalProcessor(check_market_hours=False, dedup_window_seconds=1)
        sig1 = _make_signal()
        proc.process(sig1)

        # Simulate time passing beyond dedup window
        past_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        key = (sig1.symbol, sig1.exchange, sig1.action)
        proc._seen[key] = past_time

        sig2 = _make_signal()  # Same symbol/action
        result = proc.process(sig2)
        assert result is not None


class TestMarketHoursFilter:
    def test_market_closed_raises_when_filter_enabled(self):
        proc = SignalProcessor(check_market_hours=True)
        sig = _make_signal()
        # Market hours check — Saturday midday IST should be closed
        with patch("algotrader.signals.processor.MarketHours.is_open", return_value=False):
            with pytest.raises(SignalFilteredError, match="market.*closed|closed"):
                proc.process(sig)

    def test_signal_passes_when_market_open(self):
        proc = SignalProcessor(check_market_hours=True, dedup_window_seconds=0)
        sig = _make_signal()
        with patch("algotrader.signals.processor.MarketHours.is_open", return_value=True):
            result = proc.process(sig)
        assert result is not None

    def test_market_hours_not_checked_when_disabled(self):
        proc = SignalProcessor(check_market_hours=False)
        sig = _make_signal()
        # No mock needed — should pass regardless of market hours
        result = proc.process(sig)
        assert result is not None


class TestProcessorState:
    def test_seen_signals_cleared(self):
        proc = SignalProcessor(check_market_hours=False, dedup_window_seconds=60)
        proc.process(_make_signal())
        proc.clear()
        result = proc.process(_make_signal())
        assert result is not None
