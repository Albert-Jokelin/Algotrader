"""Upstox live/sandbox broker.

Wraps the upstoxlite package to implement the IBroker interface.
Maps between internal models and the Upstox v2 API format.

Phase-2 enhancements
---------------------
* available_capital  — fetches from GET /v2/user/fund-and-margin with a 60s
                       cache to avoid rate-limit exhaustion.
* submit_order       — uses order.product ("MIS"/"NRML"/"CNC") instead of
                       the previously hardcoded "D".
* _map_symbol        — delegates to InstrumentMaster instead of a static dict.

Edge constraints
----------------
* Fund balance: the correct field is equity.available_margin (not total_balance,
  which includes pledged collateral that can't fund new orders).
* Cache invalidation: the capital cache is cleared after every fill so the
  available margin reflects the latest state.
* Product mapping: MIS→"I", NRML→"D", CNC→"D".  Unknown values fall back to "D".
* InstrumentMaster is loaded lazily on first symbol lookup; failures fall back
  to the inline hardcoded map.
"""

from __future__ import annotations

import logging
import sys
import os
import time
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

log = logging.getLogger(__name__)

# ── Product code mapping ───────────────────────────────────────────────────────
# Internal model product → Upstox API product code
_PRODUCT_MAP: Dict[str, str] = {
    "MIS":  "I",   # Margin Intraday Square-off
    "NRML": "D",   # Normal (delivery / overnight)
    "CNC":  "D",   # Cash-and-Carry (delivery, no leverage)
}

# ── Order type and side mappings ───────────────────────────────────────────────
_ORDER_TYPE_MAP: Dict[OrderType, str] = {
    OrderType.MARKET:      "MARKET",
    OrderType.LIMIT:       "LIMIT",
    OrderType.STOP_MARKET: "SL-M",
    OrderType.STOP_LIMIT:  "SL",
}

_SIDE_MAP: Dict[OrderSide, str] = {
    OrderSide.BUY:  "BUY",
    OrderSide.SELL: "SELL",
}

# Capital cache TTL: 60 seconds
_CAPITAL_CACHE_TTL = 60.0


def _try_import_upstoxlite():
    """Try to import upstoxlite; return (None, None) if unavailable."""
    try:
        pkg_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "upstoxlite_package"
        )
        sys.path.insert(0, os.path.abspath(pkg_path))
        from upstoxlite import UpstoxSyncClient, UpstoxConfig as UConfig  # type: ignore
        return UpstoxSyncClient, UConfig
    except ImportError:
        return None, None


class UpstoxBroker(IBroker):
    """Live/sandbox broker using the Upstox v2 API."""

    def __init__(
        self,
        config: UpstoxConfig,
        instrument_master=None,
    ) -> None:
        self._config = config
        self._client = self._build_client(config)
        self._open_orders: Dict[str, Order] = {}

        # Fund balance cache
        self._cash: float = 0.0
        self._capital_fetched_at: float = 0.0

        # InstrumentMaster — lazy-loaded if not provided
        self._instrument_master = instrument_master

        # Idempotency guard: set of internal order_ids already sent to Upstox.
        # Prevents double-submission when a response is lost (e.g. network drop
        # right after the API call returns 200 but before we parse the body).
        self._submitted_ids: set = set()

    # ── IBroker interface ──────────────────────────────────────────────────────

    def submit_order(
        self, order: Order, market_price: float = 0.0, bar=None
    ) -> Optional[Fill]:
        # ── Idempotency guard ──────────────────────────────────────────────────
        # If this order_id was already submitted (response may have been lost),
        # do NOT re-submit — reconcile against the live order book instead.
        if order.order_id in self._submitted_ids:
            log.warning(
                "Duplicate submission detected for order %s — reconciling instead "
                "of placing a new order. Call reconcile_open_orders() to sync state.",
                order.order_id,
            )
            raise OrderRejectedError(
                f"Duplicate submission: order {order.order_id} was already sent "
                "to Upstox. Call reconcile_open_orders() to check its status."
            )

        instrument_key = self._map_symbol(order.symbol, order.exchange)
        upstox_product = _PRODUCT_MAP.get(order.product.upper(), "D")

        # Use the internal order_id (UUID) as the Upstox tag for idempotency.
        # Upstox limits tag to 20 chars; the first 20 of a UUID are unique enough
        # within a trading session.
        client_tag = order.order_id[:20]

        payload = {
            "quantity":           order.quantity,
            "product":            upstox_product,
            "validity":           order.validity,
            "price":              order.price or 0,
            "tag":                client_tag,
            "instrument_token":   instrument_key,
            "order_type":         _ORDER_TYPE_MAP[order.order_type],
            "transaction_type":   _SIDE_MAP[order.side],
            "disclosed_quantity": 0,
            "trigger_price":      order.stop_price or 0,
            "is_amo":             False,
        }

        # Mark as submitted BEFORE the API call.  If the network drops after the
        # exchange accepts the order but before we receive the HTTP response, the
        # idempotency guard will prevent a duplicate on the next retry.
        self._submitted_ids.add(order.order_id)

        try:
            response = self._client.place_order(**payload)
        except Exception as exc:
            # Keep the order in _submitted_ids so a retry will be caught.
            raise BrokerConnectionError(f"Upstox API error: {exc}") from exc

        status = response.get("status", "").upper()
        if status in ("REJECTED", "CANCELLED"):
            msg = response.get("status_message", "Order rejected")
            raise OrderRejectedError(f"Upstox rejected order: {msg}")

        order.broker_order_id = response.get("order_id")

        if status == "COMPLETE":
            avg_price = float(
                response.get("average_price", market_price) or market_price
            )
            qty_filled = int(
                response.get("filled_quantity", order.quantity) or order.quantity
            )
            order.status = OrderStatus.FILLED
            fill = Fill(
                order_id=order.order_id,
                symbol=order.symbol,
                exchange=order.exchange,
                side=order.side,
                quantity=qty_filled,
                price=avg_price,
            )
            # Invalidate capital cache so next read picks up new margin state
            self._capital_fetched_at = 0.0
            return fill

        order.status = OrderStatus.OPEN
        self._open_orders[order.order_id] = order
        return None

    def cancel_order(self, order_id: str) -> bool:
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
            qty = p.get("quantity", 0)
            if qty == 0:
                continue
            try:
                exch = Exchange(p.get("exchange", "NSE"))
            except ValueError:
                exch = Exchange.NSE
            positions.append(
                Position(
                    symbol=p.get("tradingsymbol", ""),
                    exchange=exch,
                    quantity=qty,
                    average_price=float(p.get("average_price", 0.0) or 0.0),
                    strategy_name="",
                )
            )
        return positions

    def get_open_orders(self) -> List[Order]:
        return list(self._open_orders.values())

    def process_open_order(self, order: Order, bar) -> Optional[Fill]:
        """Not used in live mode — OrderPoller handles status updates asynchronously."""
        return None

    def reconcile_open_orders(self) -> int:
        """Sync local open-order state against the live Upstox order book.

        Call this at bot startup (after a crash or reconnect) to detect orders
        that were submitted but whose responses were never received.

        Algorithm
        ---------
        1. Fetch all orders from the Upstox API (GET /v2/orders).
        2. For each local open order:
           a. Match by ``broker_order_id`` if set, else by ``tag`` prefix
              (the first 20 chars of ``order.order_id``).
           b. If a match is found, update ``order.status`` from the API response.
           c. Remove filled / cancelled / rejected orders from ``_open_orders``.
        3. Return the number of orders that were reconciled (status updated).

        Returns:
            Number of local orders whose status was updated from the API.
        """
        try:
            api_orders = self._client.get_orders() or []
        except Exception as exc:
            log.warning("reconcile_open_orders: could not fetch orders — %s", exc)
            return 0

        # Build a lookup index: broker_order_id → api_order dict
        by_broker_id: Dict[str, dict] = {}
        by_tag: Dict[str, dict] = {}
        for ao in api_orders:
            bid = ao.get("order_id") or ao.get("broker_order_id", "")
            tag = (ao.get("tag") or "")[:20]
            if bid:
                by_broker_id[str(bid)] = ao
            if tag:
                by_tag[tag] = ao

        _TERMINAL_API_STATUSES = frozenset({
            "COMPLETE", "FILLED", "CANCELLED", "REJECTED",
        })

        reconciled = 0
        to_remove = []

        for oid, order in list(self._open_orders.items()):
            # Match: prefer broker_order_id, fall back to tag prefix
            ao = None
            if order.broker_order_id:
                ao = by_broker_id.get(str(order.broker_order_id))
            if ao is None:
                ao = by_tag.get(order.order_id[:20])

            if ao is None:
                log.debug(
                    "reconcile: no Upstox match for local order %s — "
                    "may have been submitted but not yet visible in order book",
                    oid,
                )
                continue

            api_status = (ao.get("status") or "").upper()
            prev_status = order.status

            if api_status in ("COMPLETE", "FILLED"):
                order.status = OrderStatus.FILLED
                order.broker_order_id = ao.get("order_id") or order.broker_order_id
                to_remove.append(oid)
            elif api_status in ("CANCELLED", "REJECTED"):
                order.status = (
                    OrderStatus.CANCELLED if api_status == "CANCELLED"
                    else OrderStatus.REJECTED
                )
                to_remove.append(oid)
            elif api_status in ("OPEN", "TRIGGER PENDING", "AMO REQ RECEIVED"):
                order.status = OrderStatus.OPEN
                order.broker_order_id = ao.get("order_id") or order.broker_order_id
            elif api_status == "PARTIALLY_FILLED":
                order.status = OrderStatus.PARTIALLY_FILLED
                order.filled_quantity = int(ao.get("filled_quantity", 0) or 0)

            if order.status != prev_status:
                reconciled += 1
                log.info(
                    "reconcile: order %s status %s → %s",
                    oid[:8], prev_status.value, order.status.value,
                )

        for oid in to_remove:
            self._open_orders.pop(oid, None)

        log.info(
            "reconcile_open_orders: %d local orders checked, %d updated, %d removed",
            len(self._open_orders) + len(to_remove), reconciled, len(to_remove),
        )
        return reconciled

    @property
    def available_capital(self) -> float:
        """Return cached available equity margin; refresh if cache is stale (> 60 s)."""
        if time.time() - self._capital_fetched_at >= _CAPITAL_CACHE_TTL:
            self._refresh_capital()
        return self._cash

    @property
    def realised_pnl(self) -> float:
        return 0.0  # Fetched from Upstox P&L API in a future phase

    # ── Capital refresh ────────────────────────────────────────────────────────

    def _refresh_capital(self) -> None:
        """Fetch available margin from GET /v2/user/fund-and-margin."""
        try:
            resp = self._client.get_fund_and_margin()
            equity = (resp or {}).get("equity", {})
            # Use available_margin (tradeable cash), not total_balance
            available = float(equity.get("available_margin", 0.0) or 0.0)
            self._cash = available
            self._capital_fetched_at = time.time()
            log.debug("Capital refreshed: ₹%.2f available", self._cash)
        except Exception as exc:
            log.warning(
                "Fund balance refresh failed: %s — using stale value ₹%.2f",
                exc, self._cash,
            )

    # ── Instrument key resolution ──────────────────────────────────────────────

    def _map_symbol(self, symbol: str, exchange: Exchange) -> str:
        """Resolve a trading symbol to a Upstox instrument_key."""
        if self._instrument_master is None:
            self._instrument_master = self._load_instrument_master()
        try:
            return self._instrument_master.get_instrument_key(
                symbol, exchange.value
            )
        except Exception:
            # Graceful fallback: construct a key; API will reject it if wrong.
            log.warning(
                "Instrument key not found for %s:%s — using guessed key",
                exchange.value, symbol,
            )
            return f"NSE_EQ|{symbol}"

    @staticmethod
    def _load_instrument_master():
        try:
            from algotrader.data.instrument_master import InstrumentMaster
            master = InstrumentMaster()
            master.load()
            return master
        except Exception as exc:
            log.warning("InstrumentMaster unavailable: %s — using fallback", exc)
            return _FallbackInstrumentMaster()

    # ── Client builder ─────────────────────────────────────────────────────────

    @staticmethod
    def _build_client(config: UpstoxConfig):
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

        mock = MagicMock()
        mock.place_order.side_effect = BrokerConnectionError(
            "upstoxlite package not available"
        )
        mock.get_fund_and_margin.return_value = {
            "equity": {"available_margin": 0.0}
        }
        return mock


class _FallbackInstrumentMaster:
    """Hardcoded instrument map used when InstrumentMaster cannot be loaded."""

    _MAP = {
        ("RELIANCE",   "NSE"): "NSE_EQ|INE002A01018",
        ("INFY",       "NSE"): "NSE_EQ|INE009A01021",
        ("TCS",        "NSE"): "NSE_EQ|INE467B01029",
        ("HDFC",       "NSE"): "NSE_EQ|INE001A01036",
        ("ICICIBANK",  "NSE"): "NSE_EQ|INE090A01021",
        ("SBIN",       "NSE"): "NSE_EQ|INE062A01020",
        ("WIPRO",      "NSE"): "NSE_EQ|INE075A01022",
        ("HCLTECH",    "NSE"): "NSE_EQ|INE860A01027",
        ("TATAMOTORS", "NSE"): "NSE_EQ|INE155A01022",
        ("BAJFINANCE", "NSE"): "NSE_EQ|INE296A01024",
    }

    def get_instrument_key(self, symbol: str, exchange: str) -> str:
        result = self._MAP.get((symbol.upper(), exchange.upper()))
        if result:
            return result
        from algotrader.exceptions import InstrumentNotFoundError
        raise InstrumentNotFoundError(
            f"No instrument key for {exchange}:{symbol} in fallback map"
        )
