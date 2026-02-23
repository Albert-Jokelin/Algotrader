"""Abstract broker interface (IBroker).

All broker implementations (paper, Upstox, etc.) must inherit from this.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from algotrader.signals.models import Fill, Order, Position


class IBroker(ABC):
    """Abstract broker interface."""

    @abstractmethod
    def submit_order(self, order: Order, market_price: float = 0.0) -> Optional[Fill]:
        """Submit an order. Returns a Fill if immediately executed, else None."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True on success."""

    @abstractmethod
    def get_positions(self) -> List[Position]:
        """Return all open positions."""

    @abstractmethod
    def get_open_orders(self) -> List[Order]:
        """Return all pending/open orders."""

    @property
    @abstractmethod
    def available_capital(self) -> float:
        """Cash available for new orders."""

    @property
    @abstractmethod
    def realised_pnl(self) -> float:
        """Total realised profit/loss (session)."""
