"""TDD tests for trading signal data models.

Written BEFORE the implementation (Red phase).
"""

import pytest
from datetime import datetime, timezone

from algotrader.signals.models import (
    SignalAction,
    SignalSource,
    OrderType,
    Exchange,
    TradingSignal,
    Order,
    OrderSide,
    OrderStatus,
    Position,
    Fill,
)


# ── SignalAction enum ─────────────────────────────────────────────────────────

class TestSignalAction:
    def test_buy_value(self):
        assert SignalAction.BUY == "BUY"

    def test_sell_value(self):
        assert SignalAction.SELL == "SELL"

    def test_exit_long_value(self):
        assert SignalAction.EXIT_LONG == "EXIT_LONG"

    def test_exit_short_value(self):
        assert SignalAction.EXIT_SHORT == "EXIT_SHORT"

    def test_exit_value(self):
        assert SignalAction.EXIT == "EXIT"

    def test_from_string_case_insensitive(self):
        assert SignalAction("BUY") == SignalAction.BUY


# ── Exchange enum ─────────────────────────────────────────────────────────────

class TestExchange:
    def test_nse(self):
        assert Exchange.NSE == "NSE"

    def test_bse(self):
        assert Exchange.BSE == "BSE"

    def test_nfo(self):
        assert Exchange.NFO == "NFO"  # NSE F&O


# ── TradingSignal model ───────────────────────────────────────────────────────

class TestTradingSignal:
    def _make_signal(self, **overrides):
        defaults = dict(
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            action=SignalAction.BUY,
            strategy_name="EMA_Cross",
            source=SignalSource.PINE_SCRIPT,
        )
        defaults.update(overrides)
        return TradingSignal(**defaults)

    def test_basic_creation(self):
        sig = self._make_signal()
        assert sig.symbol == "RELIANCE"
        assert sig.exchange == Exchange.NSE
        assert sig.action == SignalAction.BUY

    def test_signal_id_auto_generated(self):
        sig = self._make_signal()
        assert sig.signal_id is not None
        assert len(sig.signal_id) > 0

    def test_timestamp_auto_generated(self):
        sig = self._make_signal()
        assert sig.timestamp is not None
        assert isinstance(sig.timestamp, datetime)

    def test_optional_price_defaults_to_none(self):
        sig = self._make_signal()
        assert sig.price is None

    def test_price_can_be_set(self):
        sig = self._make_signal(price=2850.50)
        assert sig.price == pytest.approx(2850.50)

    def test_stop_loss_defaults_to_none(self):
        sig = self._make_signal()
        assert sig.stop_loss is None

    def test_take_profit_defaults_to_none(self):
        sig = self._make_signal()
        assert sig.take_profit is None

    def test_quantity_defaults_to_none(self):
        sig = self._make_signal()
        assert sig.quantity is None

    def test_is_entry_buy(self):
        sig = self._make_signal(action=SignalAction.BUY)
        assert sig.is_entry is True
        assert sig.is_exit is False

    def test_is_entry_sell(self):
        sig = self._make_signal(action=SignalAction.SELL)
        assert sig.is_entry is True
        assert sig.is_exit is False

    def test_is_exit_for_exit_long(self):
        sig = self._make_signal(action=SignalAction.EXIT_LONG)
        assert sig.is_exit is True
        assert sig.is_entry is False

    def test_is_exit_for_exit_short(self):
        sig = self._make_signal(action=SignalAction.EXIT_SHORT)
        assert sig.is_exit is True

    def test_is_exit_for_exit(self):
        sig = self._make_signal(action=SignalAction.EXIT)
        assert sig.is_exit is True

    def test_metadata_defaults_to_empty_dict(self):
        sig = self._make_signal()
        assert sig.metadata == {}

    def test_metadata_can_be_set(self):
        sig = self._make_signal(metadata={"bar_index": 150})
        assert sig.metadata["bar_index"] == 150

    def test_full_symbol_nse(self):
        sig = self._make_signal(symbol="INFY", exchange=Exchange.NSE)
        assert sig.full_symbol == "NSE:INFY"

    def test_full_symbol_bse(self):
        sig = self._make_signal(symbol="RELIANCE", exchange=Exchange.BSE)
        assert sig.full_symbol == "BSE:RELIANCE"


# ── Order model ───────────────────────────────────────────────────────────────

class TestOrder:
    def _make_order(self, **overrides):
        defaults = dict(
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=10,
            strategy_name="EMA_Cross",
        )
        defaults.update(overrides)
        return Order(**defaults)

    def test_basic_creation(self):
        order = self._make_order()
        assert order.symbol == "RELIANCE"
        assert order.side == OrderSide.BUY
        assert order.quantity == 10

    def test_order_id_auto_generated(self):
        order = self._make_order()
        assert order.order_id is not None

    def test_initial_status_is_pending(self):
        order = self._make_order()
        assert order.status == OrderStatus.PENDING

    def test_limit_order_requires_price(self):
        with pytest.raises(ValueError, match="price"):
            self._make_order(order_type=OrderType.LIMIT, price=None)

    def test_limit_order_with_price_ok(self):
        order = self._make_order(order_type=OrderType.LIMIT, price=2850.0)
        assert order.price == pytest.approx(2850.0)

    def test_market_order_price_is_optional(self):
        order = self._make_order(order_type=OrderType.MARKET)
        assert order.price is None

    def test_stop_loss_optional(self):
        order = self._make_order(stop_loss=2800.0)
        assert order.stop_loss == pytest.approx(2800.0)

    def test_take_profit_optional(self):
        order = self._make_order(take_profit=2950.0)
        assert order.take_profit == pytest.approx(2950.0)

    def test_is_terminal_pending(self):
        order = self._make_order()
        assert order.is_terminal is False

    def test_is_terminal_filled(self):
        order = self._make_order()
        order.status = OrderStatus.FILLED
        assert order.is_terminal is True

    def test_is_terminal_cancelled(self):
        order = self._make_order()
        order.status = OrderStatus.CANCELLED
        assert order.is_terminal is True

    def test_is_terminal_rejected(self):
        order = self._make_order()
        order.status = OrderStatus.REJECTED
        assert order.is_terminal is True


# ── Fill model ────────────────────────────────────────────────────────────────

class TestFill:
    def test_basic_fill(self):
        fill = Fill(
            order_id="ord-123",
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            side=OrderSide.BUY,
            quantity=10,
            price=2850.0,
        )
        assert fill.quantity == 10
        assert fill.price == pytest.approx(2850.0)

    def test_fill_value(self):
        fill = Fill(
            order_id="ord-123",
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            side=OrderSide.BUY,
            quantity=10,
            price=2850.0,
        )
        assert fill.value == pytest.approx(28500.0)


# ── Position model ────────────────────────────────────────────────────────────

class TestPosition:
    def test_basic_position(self):
        pos = Position(
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            quantity=10,
            average_price=2800.0,
            strategy_name="EMA_Cross",
        )
        assert pos.quantity == 10
        assert pos.average_price == pytest.approx(2800.0)

    def test_cost_basis(self):
        pos = Position(
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            quantity=10,
            average_price=2800.0,
            strategy_name="EMA_Cross",
        )
        assert pos.cost_basis == pytest.approx(28000.0)

    def test_unrealised_pnl(self):
        pos = Position(
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            quantity=10,
            average_price=2800.0,
            strategy_name="EMA_Cross",
        )
        assert pos.unrealised_pnl(current_price=2900.0) == pytest.approx(1000.0)

    def test_unrealised_pnl_loss(self):
        pos = Position(
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            quantity=10,
            average_price=2800.0,
            strategy_name="EMA_Cross",
        )
        assert pos.unrealised_pnl(current_price=2700.0) == pytest.approx(-1000.0)

    def test_short_position_pnl(self):
        pos = Position(
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            quantity=-10,
            average_price=2800.0,
            strategy_name="EMA_Cross",
        )
        # Short: profit when price falls
        assert pos.unrealised_pnl(current_price=2700.0) == pytest.approx(1000.0)

    def test_is_long(self):
        pos = Position(
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            quantity=10,
            average_price=2800.0,
            strategy_name="EMA_Cross",
        )
        assert pos.is_long is True
        assert pos.is_short is False

    def test_is_short(self):
        pos = Position(
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            quantity=-10,
            average_price=2800.0,
            strategy_name="EMA_Cross",
        )
        assert pos.is_short is True
        assert pos.is_long is False
