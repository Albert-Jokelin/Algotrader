"""Paper trading broker.

Simulates order execution locally without real API calls.
Maintains:
  - Available cash (deducted on buy, credited on sell)
  - Open positions (quantity, average price)
  - Open limit/stop orders (pending execution)
  - Fill history
  - Realised P&L
"""

from __future__ import annotations

from typing import Dict, List, Optional

from algotrader.broker.base import IBroker
from algotrader.exceptions import OrderNotFoundError
from algotrader.signals.models import (
    Exchange,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)


class PaperBroker(IBroker):
    """Simulated broker for backtesting and paper trading."""

    def __init__(self, initial_capital: float) -> None:
        self._initial_capital = initial_capital
        self._cash = initial_capital

        # symbol → Position
        self._positions: Dict[str, Position] = {}

        # order_id → Order (pending limit/stop orders)
        self._open_orders: Dict[str, Order] = {}

        # All fills, in chronological order
        self._fills: List[Fill] = []

        # Total realised PnL
        self._realised_pnl: float = 0.0

    # ── IBroker interface ──────────────────────────────────────────────────────

    def submit_order(self, order: Order, market_price: float = 0.0) -> Optional[Fill]:
        """Submit an order. Market orders fill immediately; limit orders queue."""
        if order.order_type == OrderType.MARKET:
            return self._fill_immediately(order, fill_price=market_price)

        if order.order_type == OrderType.LIMIT:
            return self._try_fill_limit(order, market_price)

        # Other types queue (simplified)
        self._open_orders[order.order_id] = order
        return None

    def cancel_order(self, order_id: str) -> bool:
        if order_id not in self._open_orders:
            raise OrderNotFoundError(f"Order not found: {order_id}")
        order = self._open_orders.pop(order_id)
        order.status = OrderStatus.CANCELLED
        return True

    def get_positions(self) -> List[Position]:
        return [p for p in self._positions.values() if p.quantity != 0]

    def get_open_orders(self) -> List[Order]:
        return list(self._open_orders.values())

    @property
    def available_capital(self) -> float:
        return self._cash

    @property
    def realised_pnl(self) -> float:
        return self._realised_pnl

    @property
    def fills(self) -> List[Fill]:
        return self._fills

    # ── Order execution ────────────────────────────────────────────────────────

    def _fill_immediately(self, order: Order, fill_price: float) -> Fill:
        cost = fill_price * order.quantity
        if order.side == OrderSide.BUY:
            if cost > self._cash:
                raise ValueError(
                    f"Insufficient capital: need {cost:.2f}, have {self._cash:.2f}"
                )
            self._cash -= cost
            self._update_position_buy(order, fill_price)
        else:
            pnl = self._update_position_sell(order, fill_price)
            self._realised_pnl += pnl
            self._cash += fill_price * order.quantity

        fill = Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            exchange=order.exchange,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
        )
        self._fills.append(fill)
        order.status = OrderStatus.FILLED
        order.filled_quantity = order.quantity
        order.average_fill_price = fill_price
        return fill

    def _try_fill_limit(self, order: Order, market_price: float) -> Optional[Fill]:
        assert order.price is not None

        if order.side == OrderSide.BUY and market_price <= order.price:
            return self._fill_immediately(order, fill_price=order.price)

        if order.side == OrderSide.SELL and market_price >= order.price:
            return self._fill_immediately(order, fill_price=order.price)

        # Not yet triggered — queue as open order
        self._open_orders[order.order_id] = order
        order.status = OrderStatus.OPEN
        return None

    # ── Position management ────────────────────────────────────────────────────

    def _position_key(self, order: Order) -> str:
        return f"{order.exchange.value}:{order.symbol}:{order.strategy_name}"

    def _update_position_buy(self, order: Order, price: float) -> None:
        key = self._position_key(order)
        if key in self._positions:
            pos = self._positions[key]
            total_cost = pos.quantity * pos.average_price + order.quantity * price
            total_qty = pos.quantity + order.quantity
            pos.average_price = total_cost / total_qty
            pos.quantity = total_qty
        else:
            self._positions[key] = Position(
                symbol=order.symbol,
                exchange=order.exchange,
                quantity=order.quantity,
                average_price=price,
                strategy_name=order.strategy_name,
            )

    def _update_position_sell(self, order: Order, price: float) -> float:
        """Update position on sell and return realised PnL."""
        key = self._position_key(order)
        pnl = 0.0

        if key in self._positions:
            pos = self._positions[key]
            pnl = (price - pos.average_price) * order.quantity
            pos.quantity -= order.quantity
            if pos.quantity == 0:
                del self._positions[key]
        return pnl
