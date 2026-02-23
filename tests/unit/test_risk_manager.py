"""TDD tests for risk manager.

The risk manager gates signals through exposure/loss limits.
Written BEFORE the implementation.
"""

import pytest
from algotrader.risk.manager import RiskManager, RiskDecision
from algotrader.signals.models import (
    Exchange, Position, SignalAction, SignalSource, TradingSignal
)
from algotrader.config import RiskConfig
from algotrader.exceptions import (
    DailyLossLimitExceededError,
    PortfolioExposureExceededError,
    PositionLimitExceededError,
)


def _sig(symbol="RELIANCE", action=SignalAction.BUY, price=2000.0):
    return TradingSignal(
        symbol=symbol,
        exchange=Exchange.NSE,
        action=action,
        strategy_name="test",
        source=SignalSource.PINE_SCRIPT,
        price=price,
    )


def _make_rm(
    capital=100_000.0,
    max_pos_pct=0.10,
    max_exposure_pct=0.80,
    max_daily_loss_pct=0.02,
):
    cfg = RiskConfig(
        max_position_pct=max_pos_pct,
        max_portfolio_exposure_pct=max_exposure_pct,
        max_daily_loss_pct=max_daily_loss_pct,
        default_stop_loss_pct=0.02,
    )
    return RiskManager(config=cfg, capital=capital)


class TestRiskDecision:
    def test_approve_simple_signal(self):
        rm = _make_rm()
        decision = rm.evaluate(_sig(), positions=[])
        assert decision.approved is True

    def test_decision_includes_quantity(self):
        rm = _make_rm(capital=100_000)
        decision = rm.evaluate(_sig(price=2000.0), positions=[])
        assert decision.quantity > 0

    def test_decision_includes_stop_loss(self):
        rm = _make_rm()
        sig = _sig(price=2000.0)
        sig = sig.model_copy(update={"stop_loss": None})  # no stop in signal
        decision = rm.evaluate(sig, positions=[])
        # Should compute a default stop loss
        assert decision.stop_loss is not None
        assert decision.stop_loss < 2000.0


class TestPositionLimit:
    def test_exceeds_single_position_limit(self):
        # max_position_pct=0.05 → max 5,000 on a 100k capital
        # Signal: 100 shares × 2000 = 200,000 > 5,000
        rm = _make_rm(capital=100_000, max_pos_pct=0.05)
        sig = _sig(price=2000.0)
        # Manually set quantity that would exceed limit
        sig = sig.model_copy(update={"quantity": 100})
        decision = rm.evaluate(sig, positions=[])
        # Should be approved but with reduced quantity
        assert decision.approved is True
        assert decision.quantity * 2000.0 <= 100_000 * 0.05 + 0.01  # within limit


class TestPortfolioExposure:
    def test_rejects_when_exposure_at_max(self):
        rm = _make_rm(capital=100_000, max_exposure_pct=0.80)
        # Simulate 80% already deployed
        positions = [
            Position(
                symbol="HDFC",
                exchange=Exchange.NSE,
                quantity=40,
                average_price=2000.0,
                strategy_name="test",
            )
        ]
        # 40 × 2000 = 80,000 = 80% of 100k — at limit
        # Adding any more should raise
        with pytest.raises(PortfolioExposureExceededError):
            rm.evaluate(_sig(), positions=positions)

    def test_approves_within_exposure(self):
        rm = _make_rm(capital=100_000, max_exposure_pct=0.80)
        # Only 30% deployed
        positions = [
            Position(
                symbol="HDFC",
                exchange=Exchange.NSE,
                quantity=15,
                average_price=2000.0,
                strategy_name="test",
            )
        ]
        decision = rm.evaluate(_sig(price=500.0), positions=positions)
        assert decision.approved is True


class TestDailyLossLimit:
    def test_raises_when_daily_loss_exceeded(self):
        rm = _make_rm(capital=100_000, max_daily_loss_pct=0.02)
        # Realise 2,001 loss on a 100k capital
        rm.record_realised_pnl(-2_001.0)
        with pytest.raises(DailyLossLimitExceededError):
            rm.evaluate(_sig(), positions=[])

    def test_passes_when_within_daily_loss(self):
        rm = _make_rm(capital=100_000, max_daily_loss_pct=0.02)
        rm.record_realised_pnl(-500.0)  # Only 0.5% loss
        decision = rm.evaluate(_sig(), positions=[])
        assert decision.approved is True

    def test_daily_pnl_resets(self):
        rm = _make_rm(capital=100_000, max_daily_loss_pct=0.02)
        rm.record_realised_pnl(-2_001.0)
        rm.reset_daily_pnl()
        decision = rm.evaluate(_sig(), positions=[])
        assert decision.approved is True
