"""TDD tests for paper trading broker.

The paper broker simulates order execution locally (no real API calls).
Written BEFORE the implementation.
"""

import pytest
from algotrader.broker.paper_broker import PaperBroker
from algotrader.signals.models import (
    Exchange, Fill, Order, OrderSide, OrderStatus, OrderType, Position
)
from algotrader.exceptions import OrderNotFoundError


def _market_buy(symbol="RELIANCE", qty=10, price=2000.0):
    return Order(
        symbol=symbol,
        exchange=Exchange.NSE,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=qty,
        strategy_name="test",
    )


def _limit_buy(price=2000.0, qty=10):
    return Order(
        symbol="RELIANCE",
        exchange=Exchange.NSE,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=qty,
        price=price,
        strategy_name="test",
    )


class TestPaperBrokerInitialState:
    def test_initial_capital(self):
        broker = PaperBroker(initial_capital=100_000)
        assert broker.available_capital == pytest.approx(100_000)

    def test_no_positions_initially(self):
        broker = PaperBroker(initial_capital=100_000)
        assert broker.get_positions() == []

    def test_no_orders_initially(self):
        broker = PaperBroker(initial_capital=100_000)
        assert broker.get_open_orders() == []


class TestMarketOrders:
    def test_market_buy_fills_immediately(self):
        broker = PaperBroker(initial_capital=100_000)
        order = _market_buy(qty=10)
        fill = broker.submit_order(order, market_price=2000.0)
        assert fill is not None
        assert fill.quantity == 10
        assert fill.price == pytest.approx(2000.0)

    def test_market_buy_creates_position(self):
        broker = PaperBroker(initial_capital=100_000)
        broker.submit_order(_market_buy(qty=5), market_price=2000.0)
        positions = broker.get_positions()
        assert len(positions) == 1
        assert positions[0].quantity == 5

    def test_market_buy_deducts_capital(self):
        broker = PaperBroker(initial_capital=100_000)
        broker.submit_order(_market_buy(qty=10), market_price=2000.0)
        # 10 × 2000 = 20,000 spent
        assert broker.available_capital == pytest.approx(80_000)

    def test_market_sell_closes_position(self):
        broker = PaperBroker(initial_capital=100_000)
        broker.submit_order(_market_buy(qty=10), market_price=2000.0)
        sell = Order(
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=10,
            strategy_name="test",
        )
        broker.submit_order(sell, market_price=2100.0)
        assert broker.get_positions() == []

    def test_market_sell_realises_profit(self):
        broker = PaperBroker(initial_capital=100_000)
        broker.submit_order(_market_buy(qty=10), market_price=2000.0)
        sell = Order(
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=10,
            strategy_name="test",
        )
        broker.submit_order(sell, market_price=2100.0)
        # Profit = 10 × (2100 - 2000) = 1,000
        assert broker.realised_pnl == pytest.approx(1_000.0)

    def test_market_sell_realises_loss(self):
        broker = PaperBroker(initial_capital=100_000)
        broker.submit_order(_market_buy(qty=10), market_price=2000.0)
        sell = Order(
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=10,
            strategy_name="test",
        )
        broker.submit_order(sell, market_price=1900.0)
        assert broker.realised_pnl == pytest.approx(-1_000.0)

    def test_insufficient_capital_raises(self):
        broker = PaperBroker(initial_capital=5_000)
        with pytest.raises(ValueError, match="[Ii]nsufficient"):
            broker.submit_order(_market_buy(qty=10), market_price=2000.0)


class TestLimitOrders:
    def test_limit_buy_stays_open_if_not_triggered(self):
        broker = PaperBroker(initial_capital=100_000)
        order = _limit_buy(price=1900.0)
        result = broker.submit_order(order, market_price=2000.0)
        # Limit 1900 when market is 2000 → not filled
        assert result is None
        assert len(broker.get_open_orders()) == 1

    def test_limit_buy_fills_when_price_met(self):
        broker = PaperBroker(initial_capital=100_000)
        order = _limit_buy(price=2000.0)
        fill = broker.submit_order(order, market_price=1950.0)
        assert fill is not None
        assert fill.price == pytest.approx(2000.0)

    def test_limit_buy_fills_when_price_exactly_met(self):
        broker = PaperBroker(initial_capital=100_000)
        order = _limit_buy(price=2000.0)
        fill = broker.submit_order(order, market_price=2000.0)
        assert fill is not None


class TestOrderCancellation:
    def test_cancel_open_limit_order(self):
        broker = PaperBroker(initial_capital=100_000)
        order = _limit_buy(price=1900.0)
        broker.submit_order(order, market_price=2000.0)
        broker.cancel_order(order.order_id)
        assert broker.get_open_orders() == []

    def test_cancel_nonexistent_order_raises(self):
        broker = PaperBroker(initial_capital=100_000)
        with pytest.raises(OrderNotFoundError):
            broker.cancel_order("nonexistent-id")


class TestPositionAveraging:
    def test_adds_to_existing_long(self):
        broker = PaperBroker(initial_capital=100_000)
        broker.submit_order(_market_buy(qty=5), market_price=2000.0)
        broker.submit_order(_market_buy(qty=5), market_price=2200.0)
        positions = broker.get_positions()
        assert positions[0].quantity == 10
        # Average price = (5×2000 + 5×2200) / 10 = 2100
        assert positions[0].average_price == pytest.approx(2100.0)

    def test_partial_sell_reduces_quantity(self):
        broker = PaperBroker(initial_capital=100_000)
        broker.submit_order(_market_buy(qty=10), market_price=2000.0)
        sell = Order(
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=4,
            strategy_name="test",
        )
        broker.submit_order(sell, market_price=2000.0)
        assert broker.get_positions()[0].quantity == 6


class TestTradeHistory:
    def test_fills_recorded(self):
        broker = PaperBroker(initial_capital=100_000)
        broker.submit_order(_market_buy(qty=10), market_price=2000.0)
        assert len(broker.fills) == 1

    def test_multiple_fills_recorded(self):
        broker = PaperBroker(initial_capital=100_000)
        broker.submit_order(_market_buy(qty=5), market_price=2000.0)
        broker.submit_order(_market_buy(qty=5), market_price=2100.0)
        assert len(broker.fills) == 2
