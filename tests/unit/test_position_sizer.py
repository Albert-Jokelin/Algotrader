"""TDD tests for position sizing algorithms.

Written BEFORE the implementation.
"""

import pytest
from algotrader.risk.position_sizer import PositionSizer, SizingMethod
from algotrader.signals.models import Exchange, SignalAction, SignalSource, TradingSignal


def _sig(price=1000.0, stop_loss=None):
    return TradingSignal(
        symbol="RELIANCE",
        exchange=Exchange.NSE,
        action=SignalAction.BUY,
        strategy_name="test",
        source=SignalSource.PINE_SCRIPT,
        price=price,
        stop_loss=stop_loss,
    )


class TestFixedQuantitySizer:
    def test_returns_fixed_qty(self):
        sizer = PositionSizer(method=SizingMethod.FIXED_QUANTITY, fixed_quantity=10)
        qty = sizer.calculate(signal=_sig(), capital=100_000, current_price=1000.0)
        assert qty == 10

    def test_fixed_qty_ignores_capital(self):
        sizer = PositionSizer(method=SizingMethod.FIXED_QUANTITY, fixed_quantity=5)
        qty1 = sizer.calculate(signal=_sig(), capital=50_000, current_price=1000.0)
        qty2 = sizer.calculate(signal=_sig(), capital=500_000, current_price=1000.0)
        assert qty1 == qty2 == 5

    def test_signal_qty_overrides_sizer(self):
        sizer = PositionSizer(method=SizingMethod.FIXED_QUANTITY, fixed_quantity=10)
        sig = _sig()
        sig_with_qty = sig.model_copy(update={"quantity": 3})
        qty = sizer.calculate(signal=sig_with_qty, capital=100_000, current_price=1000.0)
        assert qty == 3


class TestPercentOfCapitalSizer:
    def test_basic_percent(self):
        # 5% of 100,000 capital at price 1,000 = 5,000 / 1,000 = 5 shares
        sizer = PositionSizer(method=SizingMethod.PERCENT_OF_CAPITAL, pct_of_capital=0.05)
        qty = sizer.calculate(signal=_sig(price=1000.0), capital=100_000, current_price=1000.0)
        assert qty == 5

    def test_fractional_shares_rounded_down(self):
        # 5% of 100,000 = 5,000; at price 999: 5,000/999 = 5.005 → floor = 5
        sizer = PositionSizer(method=SizingMethod.PERCENT_OF_CAPITAL, pct_of_capital=0.05)
        qty = sizer.calculate(signal=_sig(price=999.0), capital=100_000, current_price=999.0)
        assert qty == 5

    def test_returns_zero_if_capital_too_low(self):
        sizer = PositionSizer(method=SizingMethod.PERCENT_OF_CAPITAL, pct_of_capital=0.01)
        qty = sizer.calculate(signal=_sig(price=10_000.0), capital=100, current_price=10_000.0)
        assert qty == 0

    def test_higher_pct_gives_more_shares(self):
        sizer_5 = PositionSizer(method=SizingMethod.PERCENT_OF_CAPITAL, pct_of_capital=0.05)
        sizer_10 = PositionSizer(method=SizingMethod.PERCENT_OF_CAPITAL, pct_of_capital=0.10)
        qty5 = sizer_5.calculate(signal=_sig(price=1000.0), capital=100_000, current_price=1000.0)
        qty10 = sizer_10.calculate(signal=_sig(price=1000.0), capital=100_000, current_price=1000.0)
        assert qty10 > qty5


class TestRiskBasedSizer:
    def test_risk_based_with_stop_loss(self):
        # Risk 1% of 100,000 = 1,000 INR
        # Price=1000, stop_loss=980, risk_per_share=20
        # qty = 1000 / 20 = 50 shares
        sizer = PositionSizer(method=SizingMethod.RISK_BASED, risk_pct=0.01)
        sig = _sig(price=1000.0, stop_loss=980.0)
        qty = sizer.calculate(signal=sig, capital=100_000, current_price=1000.0)
        assert qty == 50

    def test_risk_based_without_stop_loss_uses_default(self):
        # Without stop_loss, falls back to percent-of-capital approach
        sizer = PositionSizer(method=SizingMethod.RISK_BASED, risk_pct=0.01,
                              default_stop_loss_pct=0.02)
        sig = _sig(price=1000.0, stop_loss=None)
        qty = sizer.calculate(signal=sig, capital=100_000, current_price=1000.0)
        # Default stop loss 2% → risk_per_share = 20, risk_amount = 1000
        # qty = 1000 / 20 = 50
        assert qty == 50

    def test_risk_based_zero_stop_range_returns_zero(self):
        sizer = PositionSizer(method=SizingMethod.RISK_BASED, risk_pct=0.01)
        sig = _sig(price=1000.0, stop_loss=1000.0)  # stop = entry → no risk range
        qty = sizer.calculate(signal=sig, capital=100_000, current_price=1000.0)
        assert qty == 0
