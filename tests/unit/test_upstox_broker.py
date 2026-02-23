"""TDD tests for Upstox broker wrapper.

All HTTP calls are mocked — no real API interaction.
Written BEFORE the implementation.
"""

import pytest
import responses as resp_lib
from unittest.mock import patch, MagicMock

from algotrader.broker.upstox_broker import UpstoxBroker
from algotrader.signals.models import (
    Exchange, Fill, Order, OrderSide, OrderStatus, OrderType
)
from algotrader.exceptions import BrokerConnectionError, OrderRejectedError
from algotrader.config import UpstoxConfig


SANDBOX_CONFIG = UpstoxConfig(
    client_id="test_id",
    client_secret="test_secret",
    redirect_uri="https://localhost/",
    access_token="test_token",
    sandbox=True,
)


def _market_buy(symbol="NSE_EQ|INE002A01018", qty=10):
    return Order(
        symbol="RELIANCE",
        exchange=Exchange.NSE,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=qty,
        strategy_name="test",
    )


class TestUpstoxBrokerInit:
    def test_creates_successfully(self):
        broker = UpstoxBroker(config=SANDBOX_CONFIG)
        assert broker is not None

    def test_initial_positions_empty(self):
        broker = UpstoxBroker(config=SANDBOX_CONFIG)
        with patch.object(broker, "_client") as mock_client:
            mock_client.get_positions.return_value = []
            assert broker.get_positions() == []

    def test_initial_open_orders_empty(self):
        broker = UpstoxBroker(config=SANDBOX_CONFIG)
        with patch.object(broker, "_client") as mock_client:
            mock_client.get_orders.return_value = []
            assert broker.get_open_orders() == []


class TestUpstoxOrderSubmission:
    def test_submit_market_order_calls_api(self):
        broker = UpstoxBroker(config=SANDBOX_CONFIG)
        with patch.object(broker, "_client") as mock_client:
            mock_client.place_order.return_value = {
                "order_id": "upstox-order-123",
                "status": "COMPLETE",
            }
            order = _market_buy()
            fill = broker.submit_order(order, market_price=2000.0)
            assert mock_client.place_order.called

    def test_filled_order_returns_fill(self):
        broker = UpstoxBroker(config=SANDBOX_CONFIG)
        with patch.object(broker, "_client") as mock_client:
            mock_client.place_order.return_value = {
                "order_id": "upstox-order-123",
                "status": "COMPLETE",
                "average_price": "2000.0",
                "filled_quantity": "10",
            }
            order = _market_buy(qty=10)
            fill = broker.submit_order(order, market_price=2000.0)
            assert fill is not None

    def test_api_error_raises_broker_error(self):
        broker = UpstoxBroker(config=SANDBOX_CONFIG)
        with patch.object(broker, "_client") as mock_client:
            mock_client.place_order.side_effect = Exception("API error")
            with pytest.raises(BrokerConnectionError):
                broker.submit_order(_market_buy(), market_price=2000.0)

    def test_rejected_order_raises(self):
        broker = UpstoxBroker(config=SANDBOX_CONFIG)
        with patch.object(broker, "_client") as mock_client:
            mock_client.place_order.return_value = {
                "order_id": "upstox-order-123",
                "status": "REJECTED",
                "status_message": "Insufficient funds",
            }
            with pytest.raises(OrderRejectedError):
                broker.submit_order(_market_buy(), market_price=2000.0)


class TestUpstoxOrderCancellation:
    def test_cancel_order_calls_api(self):
        broker = UpstoxBroker(config=SANDBOX_CONFIG)
        with patch.object(broker, "_client") as mock_client:
            mock_client.cancel_order.return_value = {"status": "success"}
            result = broker.cancel_order("upstox-order-123")
            assert mock_client.cancel_order.called

    def test_cancel_order_returns_true_on_success(self):
        broker = UpstoxBroker(config=SANDBOX_CONFIG)
        with patch.object(broker, "_client") as mock_client:
            mock_client.cancel_order.return_value = {"status": "success"}
            result = broker.cancel_order("upstox-order-123")
            assert result is True


class TestSymbolMapping:
    def test_nse_symbol_mapped_to_instrument_key(self):
        """RELIANCE on NSE should map to its Upstox instrument key."""
        broker = UpstoxBroker(config=SANDBOX_CONFIG)
        order = _market_buy()
        key = broker._map_symbol(order.symbol, order.exchange)
        # Should be a non-empty string in Upstox format
        assert isinstance(key, str)
        assert len(key) > 0
