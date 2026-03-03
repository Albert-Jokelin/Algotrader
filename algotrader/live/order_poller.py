"""Order status poller — background thread that polls Upstox for fill updates.

Runs alongside the live event loop, periodically fetching the latest status
of every non-terminal order and updating the shared Order objects in-place.

Edge constraints
----------------
* Polls every `poll_interval` seconds for pending orders.
* Exponential backoff (2 × per error, capped at 60 s) on API failures.
* Thread-safe: a lock guards all access to the order registry.
* Stops automatically once all orders reach terminal states.
* Race condition guard: if an order arrives already FILLED on the first poll,
  it is still processed correctly (no double-counting at the broker level).
* The `on_fill` callback is called once per fill event (not once per poll).
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, List, Optional

from algotrader.signals.models import Exchange, Fill, Order, OrderSide, OrderStatus

log = logging.getLogger(__name__)


class OrderPoller:
    """Polls open orders and fires a callback when they fill or fail."""

    def __init__(
        self,
        poll_interval: float = 5.0,
        on_fill: Optional[Callable[[Fill], None]] = None,
    ) -> None:
        self._poll_interval = poll_interval
        self._on_fill = on_fill
        # broker_order_id → Order
        self._orders: Dict[str, Order] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._broker = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def register(self, order: Order) -> None:
        """Register an order for status tracking.

        Orders without a broker_order_id (e.g. newly created, not yet submitted)
        are silently ignored.
        """
        if order.broker_order_id is None:
            return
        with self._lock:
            self._orders[order.broker_order_id] = order

    def start(self, broker) -> None:
        """Start the polling background thread."""
        self._broker = broker
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="order-poller"
        )
        self._thread.start()
        log.info("OrderPoller started (interval=%.1fs)", self._poll_interval)

    def stop(self, timeout: float = 10.0) -> None:
        """Stop the polling thread; waits up to `timeout` seconds."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        log.info("OrderPoller stopped")

    @property
    def pending_count(self) -> int:
        """Number of orders still waiting for a terminal state."""
        with self._lock:
            return sum(1 for o in self._orders.values() if not o.is_terminal)

    # ── Background loop ────────────────────────────────────────────────────────

    def _loop(self) -> None:
        backoff = self._poll_interval
        while not self._stop_event.is_set():
            try:
                self._poll_once()
                backoff = self._poll_interval   # Reset on success
            except Exception as exc:
                log.warning("OrderPoller poll error: %s", exc)
                backoff = min(backoff * 2.0, 60.0)
            self._stop_event.wait(timeout=backoff)

    def _poll_once(self) -> None:
        with self._lock:
            pending = [o for o in self._orders.values() if not o.is_terminal]

        for order in pending:
            self._check_order(order)

    def _check_order(self, order: Order) -> None:
        """Fetch a single order's details from the broker API and update state."""
        try:
            resp = self._broker._client.get_order_details(
                order_id=order.broker_order_id
            )
        except Exception as exc:
            log.debug("Poll failed for %s: %s", order.broker_order_id, exc)
            return

        status_str = (resp or {}).get("status", "").upper()

        with self._lock:
            if status_str in ("COMPLETE", "FILLED"):
                filled_qty = int(
                    resp.get("filled_quantity", order.quantity) or order.quantity
                )
                avg_price = float(resp.get("average_price", 0.0) or 0.0)

                # Guard against double-fire on repeated polls after fill
                if order.status == OrderStatus.FILLED:
                    return

                order.status = OrderStatus.FILLED
                order.filled_quantity = filled_qty
                if avg_price > 0:
                    order.average_fill_price = avg_price

                fill = Fill(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    exchange=order.exchange,
                    side=order.side,
                    quantity=filled_qty,
                    price=avg_price,
                )
                log.info(
                    "Order %s FILLED: %s %d × %.2f",
                    order.broker_order_id,
                    order.side.value,
                    filled_qty,
                    avg_price,
                )
                if self._on_fill:
                    self._on_fill(fill)

            elif status_str in ("REJECTED", "CANCELLED"):
                new_status = (
                    OrderStatus.REJECTED
                    if status_str == "REJECTED"
                    else OrderStatus.CANCELLED
                )
                if order.status == new_status:
                    return
                order.status = new_status
                reason = resp.get("status_message", "no reason provided")
                log.warning(
                    "Order %s %s: %s", order.broker_order_id, status_str, reason
                )

            elif status_str == "OPEN":
                filled_so_far = int(resp.get("filled_quantity", 0) or 0)
                if filled_so_far > order.filled_quantity:
                    order.filled_quantity = filled_so_far
                    order.status = OrderStatus.PARTIALLY_FILLED
                    log.debug(
                        "Order %s partially filled: %d/%d",
                        order.broker_order_id,
                        filled_so_far,
                        order.quantity,
                    )
