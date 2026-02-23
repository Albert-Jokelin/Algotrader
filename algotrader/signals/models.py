"""Trading signal and order data models.

All models are Pydantic for strict type safety and validation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


# ── Enumerations ──────────────────────────────────────────────────────────────

class SignalAction(str, Enum):
    """Trading signal direction."""
    BUY = "BUY"
    SELL = "SELL"
    EXIT_LONG = "EXIT_LONG"
    EXIT_SHORT = "EXIT_SHORT"
    EXIT = "EXIT"   # Close any open position for this instrument


class SignalSource(str, Enum):
    """Origin of the signal."""
    PINE_SCRIPT = "pine_script"
    INTERNAL = "internal"
    BACKTEST = "backtest"
    MANUAL = "manual"


class Exchange(str, Enum):
    """Indian stock exchanges."""
    NSE = "NSE"    # National Stock Exchange — equities
    BSE = "BSE"    # Bombay Stock Exchange — equities
    NFO = "NFO"    # NSE Futures & Options
    MCX = "MCX"    # Multi Commodity Exchange


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    STOP_LIMIT = "STOP_LIMIT"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


# ── Signal model ──────────────────────────────────────────────────────────────

def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TradingSignal(BaseModel):
    """A single actionable trading signal produced by a strategy."""

    signal_id: str = Field(default_factory=_new_uuid)
    timestamp: datetime = Field(default_factory=_utcnow)
    source: SignalSource
    symbol: str
    exchange: Exchange
    action: SignalAction
    strategy_name: str

    # Optional signal parameters (override risk manager defaults when provided).
    price: Optional[float] = None         # Entry price (None = market)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    quantity: Optional[int] = None        # Override computed position size

    metadata: dict[str, Any] = Field(default_factory=dict)

    # ── Computed helpers ──────────────────────────────────────────────────────

    @property
    def is_entry(self) -> bool:
        return self.action in (SignalAction.BUY, SignalAction.SELL)

    @property
    def is_exit(self) -> bool:
        return self.action in (
            SignalAction.EXIT_LONG,
            SignalAction.EXIT_SHORT,
            SignalAction.EXIT,
        )

    @property
    def full_symbol(self) -> str:
        """Return exchange-qualified symbol, e.g. 'NSE:RELIANCE'."""
        return f"{self.exchange.value}:{self.symbol}"


# ── Order model ───────────────────────────────────────────────────────────────

class Order(BaseModel):
    """An order submitted to a broker."""

    order_id: str = Field(default_factory=_new_uuid)
    broker_order_id: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    symbol: str
    exchange: Exchange
    side: OrderSide
    order_type: OrderType
    quantity: int
    strategy_name: str

    price: Optional[float] = None         # Required for LIMIT / STOP_LIMIT
    stop_price: Optional[float] = None    # Required for STOP_MARKET / STOP_LIMIT
    stop_loss: Optional[float] = None     # OCO stop-loss price
    take_profit: Optional[float] = None   # OCO take-profit price

    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: int = 0
    average_fill_price: Optional[float] = None

    # Link back to the triggering signal.
    signal_id: Optional[str] = None

    @model_validator(mode="after")
    def _validate_price(self) -> "Order":
        if self.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
            if self.price is None:
                raise ValueError(
                    f"price is required for {self.order_type.value} orders"
                )
        return self

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        )

    @property
    def remaining_quantity(self) -> int:
        return self.quantity - self.filled_quantity


# ── Fill model ────────────────────────────────────────────────────────────────

class Fill(BaseModel):
    """A single execution fill."""

    fill_id: str = Field(default_factory=_new_uuid)
    order_id: str
    timestamp: datetime = Field(default_factory=_utcnow)

    symbol: str
    exchange: Exchange
    side: OrderSide
    quantity: int
    price: float

    @property
    def value(self) -> float:
        """Gross fill value in INR."""
        return self.quantity * self.price


# ── Position model ────────────────────────────────────────────────────────────

class Position(BaseModel):
    """An open position held by a strategy."""

    symbol: str
    exchange: Exchange
    strategy_name: str

    quantity: int          # Positive = long, negative = short
    average_price: float

    realised_pnl: float = 0.0

    @property
    def cost_basis(self) -> float:
        return abs(self.quantity) * self.average_price

    def unrealised_pnl(self, current_price: float) -> float:
        """Unrealised P&L given a current market price."""
        return self.quantity * (current_price - self.average_price)

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0

    @property
    def full_symbol(self) -> str:
        return f"{self.exchange.value}:{self.symbol}"
