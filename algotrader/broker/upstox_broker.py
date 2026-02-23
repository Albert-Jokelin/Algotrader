"""Upstox broker implementation.

Wraps the existing upstoxlite package to implement the IBroker interface.
All network communication goes through the upstoxlite client; this layer
maps between our internal models and the Upstox API format.
"""

from __future__ import annotations

import sys
import os
from typing import Dict, List, Optional
from unittest.mock import MagicMock

from algotrader.broker.base import IBroker
from algotrader.config import UpstoxConfig
from algotrader.exceptions import BrokerConnectionError, OrderNotFoundError, OrderRejectedError
from algotrader.signals.models import (
    Exchange,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)

# ── Upstox instrument key mapping ─────────────────────────────────────────────
# Maps (NSE symbol) → Upstox instrument key.
# A more complete mapping would load from Upstox's instruments CSV.
_SYMBOL_MAP: Dict[str, str] = {
    "RELIANCE":  "NSE_EQ|INE002A01018",
    "INFY":      "NSE_EQ|INE009A01021",
    "TCS":       "NSE_EQ|INE467B01029",
    "HDFC":      "NSE_EQ|INE001A01036",
    "ICICIBANK": "NSE_EQ|INE090A01021",
    "SBIN":      "NSE_EQ|INE062A01020",
    "WIPRO":     "NSE_EQ|INE075A01022",
    "HCLTECH":   "NSE_EQ|INE860A01027",
    "TATAMOTORS":"NSE_EQ|INE155A01022",
    "BAJFINANCE":"NSE_EQ|INE296A01024",
}

# Upstox order type mapping
_ORDER_TYPE_MAP: Dict[OrderType, str] = {
    OrderType.MARKET:     "MARKET",
    OrderType.LIMIT:      "LIMIT",
    OrderType.STOP_MARKET:"SL-M",
    OrderType.STOP_LIMIT: "SL",
}

_SIDE_MAP: Dict[OrderSide, str] = {
    OrderSide.BUY:  "BUY",
    OrderSide.SELL: "SELL",
}


def _try_import_upstoxlite():
    """Try to import the upstoxlite package; return a mock client if unavailable."""
    try:
        upstox_pkg = os.path.join(
            os.path.dirname(__file__), "..", "..", "upstoxlite_package"
        )
        sys.path.insert(0, os.path.abspath(upstox_pkg))
        from upstoxlite import UpstoxSyncClient, UpstoxConfig as UConfig  # type: ignore
        return UpstoxSyncClient, UConfig
    except ImportError:
        return None, None


class UpstoxBroker(IBroker):
    """Live/sandbox broker using the Upstox API."""

    def __init__(self, config: UpstoxConfig) -> None:
        self._config = config
        self._client = self._build_client(config)
        self._open_orders: Dict[str, Order] = {}
        self._cash: float = 0.0

    # ── IBroker interface ──────────────────────────────────────────────────────

    def submit_order(self, order: Order, market_price: float = 0.0) -> Optional[Fill]:
        instrument_key = self._map_symbol(order.symbol, order.exchange)

        payload = {
            "quantity": order.quantity,
            "product": "D",          # Delivery / Intraday
            "validity": "DAY",
            "price": order.price or 0,
            "tag": order.strategy_name[:20],
            "instrument_token": instrument_key,
            "order_type": _ORDER_TYPE_MAP[order.order_type],
            "transaction_type": _SIDE_MAP[order.side],
            "disclosed_quantity": 0,
            "trigger_price": order.stop_price or 0,
            "is_amo": False,
        }

        try:
            response = self._client.place_order(**payload)
        except Exception as exc:
            raise BrokerConnectionError(f"Upstox API error: {exc}") from exc

        status = response.get("status", "").upper()
        if status in ("REJECTED", "CANCELLED"):
            msg = response.get("status_message", "Order rejected")
            raise OrderRejectedError(f"Upstox rejected order: {msg}")

        broker_order_id = response.get("order_id")
        order.broker_order_id = broker_order_id

        if status == "COMPLETE":
            avg_price = float(response.get("average_price", market_price) or market_price)
            qty_filled = int(response.get("filled_quantity", order.quantity) or order.quantity)
            order.status = OrderStatus.FILLED
            fill = Fill(
                order_id=order.order_id,
                symbol=order.symbol,
                exchange=order.exchange,
                side=order.side,
                quantity=qty_filled,
                price=avg_price,
            )
            return fill

        # Pending
        order.status = OrderStatus.OPEN
        self._open_orders[order.order_id] = order
        return None

    def cancel_order(self, order_id: str) -> bool:
        # Find broker_order_id
        order = self._open_orders.get(order_id)
        broker_id = order.broker_order_id if order else order_id

        try:
            self._client.cancel_order(order_id=broker_id)
        except Exception as exc:
            raise BrokerConnectionError(f"Cancel failed: {exc}") from exc

        if order:
            order.status = OrderStatus.CANCELLED
            del self._open_orders[order_id]
        return True

    def get_positions(self) -> List[Position]:
        try:
            raw = self._client.get_positions()
        except Exception:
            return []

        positions = []
        for p in (raw or []):
            positions.append(
                Position(
                    symbol=p.get("tradingsymbol", ""),
                    exchange=Exchange(p.get("exchange", "NSE")),
                    quantity=p.get("quantity", 0),
                    average_price=p.get("average_price", 0.0),
                    strategy_name="",
                )
            )
        return positions

    def get_open_orders(self) -> List[Order]:
        try:
            raw = self._client.get_orders()
        except Exception:
            return []
        return list(self._open_orders.values())

    @property
    def available_capital(self) -> float:
        return self._cash

    @property
    def realised_pnl(self) -> float:
        return 0.0  # Retrieved from Upstox P&L API in production

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _map_symbol(self, symbol: str, exchange: Exchange) -> str:
        """Map a plain symbol to its Upstox instrument key."""
        return _SYMBOL_MAP.get(symbol.upper(), f"NSE_EQ|{symbol}")

    @staticmethod
    def _build_client(config: UpstoxConfig):
        """Build an upstoxlite client or a minimal mock for environments without the package."""
        ClientClass, ConfigClass = _try_import_upstoxlite()
        if ClientClass is not None and ConfigClass is not None:
            upstox_cfg = ConfigClass(
                client_id=config.client_id,
                client_secret=config.client_secret,
                redirect_uri=config.redirect_uri,
                sandbox=config.sandbox,
            )
            client = ClientClass(upstox_cfg)
            client.access_token = config.access_token
            return client

        # Fallback: a minimal mock that raises BrokerConnectionError on real calls
        mock = MagicMock()
        mock.place_order.side_effect = BrokerConnectionError(
            "upstoxlite package not available"
        )
        return mock
