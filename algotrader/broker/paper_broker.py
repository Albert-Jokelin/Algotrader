"""Paper trading broker.

Simulates order execution locally without real API calls.
Maintains:
  - Available cash (deducted on buy, credited on sell)
  - Open positions (quantity, average price, trailing-SL state)
  - Open limit/stop orders (pending execution)
  - Fill history with charges
  - Realised P&L and total charges paid

Phase-1 features implemented here
----------------------------------
* Slippage          – market orders fill at ±slippage_bps away from market price.
* Brokerage         – min(flat_fee, pct × turnover) deducted per fill.
* STT & charges     – full Indian equity charge stack via ChargesCalculator.
* Partial fills     – volume_cap_pct limits how much of a bar's volume one order
                      can consume; remainder stays as an open order.
* OHLC fill logic   – limit orders trigger on bar high/low, not just close price;
                      gap-open fills happen at the open price.
* Trailing SL init  – positions get trailing_sl/hwm fields when risk_config says so;
                      the engine updates them bar-by-bar.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from algotrader.broker.base import IBroker
from algotrader.broker.charges import ChargesCalculator
from algotrader.config import RiskConfig
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

    def __init__(
        self,
        initial_capital: float,
        risk_config: Optional[RiskConfig] = None,
    ) -> None:
        self._initial_capital = initial_capital
        self._cash = initial_capital
        self._cfg = risk_config

        # symbol → Position
        self._positions: Dict[str, Position] = {}

        # order_id → Order (pending limit/stop orders and partial fills)
        self._open_orders: Dict[str, Order] = {}

        # All fills, in chronological order
        self._fills: List[Fill] = []

        # Gross realised P&L (before charges)
        self._realised_pnl: float = 0.0

        # Cumulative charges paid across all fills
        self._total_charges: float = 0.0

    # ── Configuration helpers ──────────────────────────────────────────────────

    @property
    def _slippage_bps(self) -> float:
        return self._cfg.slippage_bps if self._cfg else 0.0

    @property
    def _commission_flat(self) -> float:
        return self._cfg.commission_flat if self._cfg else 0.0

    @property
    def _commission_pct(self) -> float:
        return self._cfg.commission_pct if self._cfg else 0.0

    @property
    def _volume_cap_pct(self) -> float:
        return self._cfg.volume_cap_pct if self._cfg else 0.0

    # ── IBroker interface ──────────────────────────────────────────────────────

    def submit_order(
        self,
        order: Order,
        market_price: float = 0.0,
        bar: Optional[Dict[str, Any]] = None,
    ) -> Optional[Fill]:
        """Submit an order.

        Args:
            order:        The order to submit.
            market_price: Reference market price (typically bar close).
            bar:          Full OHLCV dict for the current bar.  When provided,
                          limit orders use high/low for trigger checks and
                          volume is used for the fill cap.

        Returns:
            A Fill if the order executed immediately, else None.
        """
        bar_volume = bar.get("volume", 0.0) if bar else 0.0

        if order.order_type == OrderType.MARKET:
            return self._fill_immediately(
                order, fill_price=market_price, bar_volume=bar_volume,
                apply_slippage=True,
            )

        if order.order_type == OrderType.LIMIT:
            fill = self._try_fill_limit(order, market_price, bar)
            # IOC: expire immediately if not filled in this bar
            if fill is None and order.validity == "IOC":
                order.status = OrderStatus.EXPIRED
                self._open_orders.pop(order.order_id, None)
                return None
            return fill

        if order.order_type == OrderType.STOP_MARKET:
            fill = self._try_fill_stop_market(order, bar, market_price)
            if fill is None and order.validity == "IOC":
                order.status = OrderStatus.EXPIRED
                self._open_orders.pop(order.order_id, None)
                return None
            return fill

        # Other order types: queue for later processing
        self._open_orders[order.order_id] = order
        order.status = OrderStatus.OPEN
        return None

    def process_open_order(
        self, order: Order, bar: Dict[str, Any]
    ) -> Optional[Fill]:
        """Try to fill a queued open order against the current bar.

        Called by the engine at the start of each bar to work through
        any pending limit, stop, or partially-filled market orders.
        """
        market_price = bar.get("close", 0.0)

        if order.order_type == OrderType.LIMIT:
            return self._try_fill_limit(order, market_price, bar)

        if order.order_type == OrderType.STOP_MARKET:
            return self._try_fill_stop_market(order, bar, market_price)

        if order.order_type == OrderType.MARKET:
            # Partially filled market order: try to complete it
            return self._fill_immediately(
                order,
                fill_price=market_price,
                bar_volume=bar.get("volume", 0.0),
                apply_slippage=True,
            )

        return None

    def cancel_order(self, order_id: str) -> bool:
        if order_id not in self._open_orders:
            raise OrderNotFoundError(f"Order not found: {order_id}")
        order = self._open_orders.pop(order_id)
        order.status = OrderStatus.CANCELLED
        return True

    def expire_day_orders(self) -> int:
        """Expire all DAY-validity open orders (call at session end: 15:30 IST).

        GTC (Good-Till-Cancelled) orders are kept and will be re-evaluated on
        the next session.  IOC orders are never queued, so this only affects DAY.

        Returns:
            Number of orders expired.
        """
        to_expire = [
            oid for oid, o in self._open_orders.items()
            if o.validity.upper() == "DAY"
        ]
        for oid in to_expire:
            order = self._open_orders.pop(oid)
            order.status = OrderStatus.EXPIRED
        return len(to_expire)

    def get_positions(self) -> List[Position]:
        return [p for p in self._positions.values() if p.quantity != 0]

    def get_open_orders(self) -> List[Order]:
        return list(self._open_orders.values())

    @property
    def available_capital(self) -> float:
        return self._cash

    @property
    def realised_pnl(self) -> float:
        """Gross realised P&L (price-difference only, before charges)."""
        return self._realised_pnl

    @property
    def net_realised_pnl(self) -> float:
        """Net realised P&L after all charges."""
        return self._realised_pnl - self._total_charges

    @property
    def total_charges(self) -> float:
        return self._total_charges

    @property
    def fills(self) -> List[Fill]:
        return self._fills

    # ── Order execution ────────────────────────────────────────────────────────

    def _fill_immediately(
        self,
        order: Order,
        fill_price: float,
        bar_volume: float = 0.0,
        apply_slippage: bool = True,
    ) -> Optional[Fill]:
        """Execute (part of) an order at fill_price.

        Applies slippage (for market orders), volume cap (partial fills),
        brokerage, and statutory charges.  Returns None only when the
        volume cap is in force and bar_volume is 0.
        """
        # ── How much can we actually fill? ─────────────────────────────────────
        remaining = order.remaining_quantity
        if self._volume_cap_pct > 0 and bar_volume > 0:
            allowed = int(bar_volume * self._volume_cap_pct)
            fill_qty = min(remaining, max(allowed, 1))
        else:
            fill_qty = remaining

        if fill_qty <= 0:
            # Nothing to fill (e.g. volume cap exhausted); keep order open
            if order.order_id not in self._open_orders:
                self._open_orders[order.order_id] = order
                order.status = OrderStatus.OPEN
            return None

        # ── Apply slippage (market orders only) ────────────────────────────────
        actual_price = fill_price
        if apply_slippage and self._slippage_bps > 0:
            factor = self._slippage_bps / 10_000.0
            if order.side == OrderSide.BUY:
                actual_price = fill_price * (1.0 + factor)
            else:
                actual_price = fill_price * (1.0 - factor)
            actual_price = max(actual_price, 0.01)  # floor at ₹0.01

        # ── Compute charges ────────────────────────────────────────────────────
        # Charges (statutory + brokerage) only apply when a RiskConfig is
        # provided.  When no config is given the broker runs in "zero-friction"
        # mode (useful for unit tests and pure P&L validation).
        if self._cfg is not None:
            charges = ChargesCalculator.compute(
                product=order.product,
                side=order.side.value,
                quantity=fill_qty,
                price=actual_price,
                commission_flat=self._commission_flat,
                commission_pct=self._commission_pct,
            )
        else:
            charges = 0.0

        # ── Cash accounting ────────────────────────────────────────────────────
        if order.side == OrderSide.BUY:
            total_cost = actual_price * fill_qty + charges
            if total_cost > self._cash:
                raise ValueError(
                    f"Insufficient capital: need ₹{total_cost:.2f}, "
                    f"have ₹{self._cash:.2f}"
                )
            self._cash -= total_cost
            self._update_position_buy(order, actual_price, fill_qty)
        else:
            pnl = self._update_position_sell(order, actual_price, fill_qty)
            self._realised_pnl += pnl
            self._cash += actual_price * fill_qty - charges

        self._total_charges += charges

        # ── Update order state ─────────────────────────────────────────────────
        prev_filled = order.filled_quantity
        order.filled_quantity += fill_qty
        # Update running average fill price
        if prev_filled == 0:
            order.average_fill_price = actual_price
        else:
            order.average_fill_price = (
                (order.average_fill_price or 0.0) * prev_filled + actual_price * fill_qty
            ) / order.filled_quantity

        if order.filled_quantity >= order.quantity:
            order.status = OrderStatus.FILLED
            self._open_orders.pop(order.order_id, None)
        else:
            order.status = OrderStatus.PARTIALLY_FILLED
            self._open_orders[order.order_id] = order

        # ── Record fill ────────────────────────────────────────────────────────
        fill = Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            exchange=order.exchange,
            side=order.side,
            quantity=fill_qty,
            price=actual_price,
            charges=charges,
        )
        self._fills.append(fill)
        return fill

    def _try_fill_limit(
        self,
        order: Order,
        market_price: float,
        bar: Optional[Dict[str, Any]] = None,
    ) -> Optional[Fill]:
        """Try to fill a limit order; use OHLC data when available."""
        assert order.price is not None
        bar_volume = bar.get("volume", 0.0) if bar else 0.0

        if bar is not None:
            bar_low  = bar.get("low",  market_price)
            bar_high = bar.get("high", market_price)
            bar_open = bar.get("open", market_price)

            if order.side == OrderSide.BUY and bar_low <= order.price:
                # Gap-down open: if we opened below the limit, fill at open
                # (buyer gets a better price than the limit).
                fill_price = min(bar_open, order.price)
                return self._fill_immediately(
                    order, fill_price=fill_price, bar_volume=bar_volume,
                    apply_slippage=False,
                )

            if order.side == OrderSide.SELL and bar_high >= order.price:
                # Gap-up open: fill at open if it's above the limit.
                fill_price = max(bar_open, order.price)
                return self._fill_immediately(
                    order, fill_price=fill_price, bar_volume=bar_volume,
                    apply_slippage=False,
                )
        else:
            # Fallback: use market_price (backward-compatible behaviour).
            if order.side == OrderSide.BUY and market_price <= order.price:
                return self._fill_immediately(
                    order, fill_price=order.price, apply_slippage=False,
                )
            if order.side == OrderSide.SELL and market_price >= order.price:
                return self._fill_immediately(
                    order, fill_price=order.price, apply_slippage=False,
                )

        # Not yet triggered — queue as open order
        if order.order_id not in self._open_orders:
            self._open_orders[order.order_id] = order
            order.status = OrderStatus.OPEN
        return None

    def _try_fill_stop_market(
        self,
        order: Order,
        bar: Optional[Dict[str, Any]],
        market_price: float = 0.0,
    ) -> Optional[Fill]:
        """Try to fill a stop-market order using bar data."""
        if order.stop_price is None:
            return None

        if bar is None:
            # No bar data: use market price as a basic trigger check
            triggered = (
                (order.side == OrderSide.SELL and market_price <= order.stop_price)
                or (order.side == OrderSide.BUY  and market_price >= order.stop_price)
            )
            if triggered:
                return self._fill_immediately(
                    order, fill_price=market_price, apply_slippage=True,
                )
            self._open_orders.setdefault(order.order_id, order)
            order.status = OrderStatus.OPEN
            return None

        bar_low  = bar.get("low",  market_price)
        bar_high = bar.get("high", market_price)
        bar_open = bar.get("open", market_price)
        bar_volume = bar.get("volume", 0.0)

        # Stop triggered: fill at open if price has gapped past stop,
        # otherwise at the stop price itself.
        if order.side == OrderSide.SELL and bar_low <= order.stop_price:
            fill_price = min(bar_open, order.stop_price)
            return self._fill_immediately(
                order, fill_price=fill_price, bar_volume=bar_volume,
                apply_slippage=True,
            )

        if order.side == OrderSide.BUY and bar_high >= order.stop_price:
            fill_price = max(bar_open, order.stop_price)
            return self._fill_immediately(
                order, fill_price=fill_price, bar_volume=bar_volume,
                apply_slippage=True,
            )

        self._open_orders.setdefault(order.order_id, order)
        order.status = OrderStatus.OPEN
        return None

    # ── Position management ────────────────────────────────────────────────────

    def _position_key(self, order: Order) -> str:
        return f"{order.exchange.value}:{order.symbol}:{order.strategy_name}"

    def _update_position_buy(
        self, order: Order, price: float, fill_qty: int
    ) -> None:
        key = self._position_key(order)
        if key in self._positions:
            pos = self._positions[key]
            total_cost = pos.quantity * pos.average_price + fill_qty * price
            total_qty  = pos.quantity + fill_qty
            pos.average_price = total_cost / total_qty
            pos.quantity = total_qty
            # Advance high-water mark if price is higher
            if price > pos.hwm:
                pos.hwm = price
        else:
            trailing_pct = self._cfg.trailing_sl_pct if self._cfg else None
            initial_sl = price * (1.0 - trailing_pct) if trailing_pct else None
            self._positions[key] = Position(
                symbol=order.symbol,
                exchange=order.exchange,
                quantity=fill_qty,
                average_price=price,
                strategy_name=order.strategy_name,
                trailing_pct=trailing_pct,
                trailing_sl=initial_sl,
                hwm=price,
            )

    def _update_position_sell(
        self, order: Order, price: float, fill_qty: int
    ) -> float:
        """Reduce position on sell; return gross realised P&L for the fill."""
        key = self._position_key(order)
        pnl = 0.0
        if key in self._positions:
            pos = self._positions[key]
            pnl = (price - pos.average_price) * fill_qty
            pos.quantity -= fill_qty
            if pos.quantity == 0:
                del self._positions[key]
        return pnl
