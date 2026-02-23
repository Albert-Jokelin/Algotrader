"""Indian stock market hours service.

NSE / BSE trading session: 09:15 – 15:30 IST, Monday–Friday.
Pre-market (order collection): 09:00 – 09:14 IST.
Post-market: 15:40 – 16:00 IST.
"""

from __future__ import annotations

from datetime import date, datetime, time
from enum import Enum
from typing import List, Optional

import pytz

IST = pytz.timezone("Asia/Kolkata")

# NSE declared market holidays for 2024 (incomplete — add more years as needed)
_NSE_HOLIDAYS_2024: List[str] = [
    "2024-01-26",  # Republic Day
    "2024-03-25",  # Holi
    "2024-03-29",  # Good Friday
    "2024-04-14",  # Dr. Ambedkar Jayanti
    "2024-04-17",  # Ram Navami
    "2024-04-21",  # Mahavir Jayanti
    "2024-05-23",  # Buddha Purnima
    "2024-06-17",  # Eid ul-Adha
    "2024-07-17",  # Muharram
    "2024-08-15",  # Independence Day
    "2024-10-02",  # Gandhi Jayanti
    "2024-10-24",  # Dussehra
    "2024-11-01",  # Diwali Laxmi Pujan
    "2024-11-15",  # Guru Nanak Jayanti
    "2024-12-25",  # Christmas
]

_MARKET_OPEN  = time(9, 15)
_MARKET_CLOSE = time(15, 30)
_PRE_OPEN     = time(9, 0)
_POST_CLOSE   = time(16, 0)


class Session(str, Enum):
    PRE_MARKET  = "pre_market"
    MARKET      = "market"
    POST_MARKET = "post_market"
    CLOSED      = "closed"


class MarketHours:
    """Utility for checking if the Indian equity market (NSE/BSE) is open."""

    def __init__(self, extra_holidays: Optional[List[str]] = None) -> None:
        self._holidays: set[date] = set()
        for d in _NSE_HOLIDAYS_2024:
            self._holidays.add(date.fromisoformat(d))
        if extra_holidays:
            for d in extra_holidays:
                self._holidays.add(date.fromisoformat(d))

    # ── Public API ─────────────────────────────────────────────────────────────

    def is_open(self) -> bool:
        """Return True if the market is currently in the regular trading session."""
        now_ist = datetime.now(IST)
        return self._is_market_session(now_ist)

    def is_open_at(self, dt: datetime, tz: str = "Asia/Kolkata") -> bool:
        """Return True if the market is in session at the given datetime."""
        src_tz = pytz.timezone(tz)
        if dt.tzinfo is None:
            dt = src_tz.localize(dt)
        else:
            dt = dt.astimezone(src_tz)
        dt_ist = dt.astimezone(IST)
        return self._is_market_session(dt_ist)

    def current_session(self) -> Session:
        """Return the current market session."""
        now_ist = datetime.now(IST)
        return self._session_at(now_ist)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _is_market_session(self, dt_ist: datetime) -> bool:
        if self._is_holiday(dt_ist.date()):
            return False
        if dt_ist.weekday() >= 5:   # Saturday=5, Sunday=6
            return False
        t = dt_ist.time()
        return _MARKET_OPEN <= t <= _MARKET_CLOSE

    def _session_at(self, dt_ist: datetime) -> Session:
        if self._is_holiday(dt_ist.date()) or dt_ist.weekday() >= 5:
            return Session.CLOSED
        t = dt_ist.time()
        if _MARKET_OPEN <= t <= _MARKET_CLOSE:
            return Session.MARKET
        if _PRE_OPEN <= t < _MARKET_OPEN:
            return Session.PRE_MARKET
        if _MARKET_CLOSE < t <= _POST_CLOSE:
            return Session.POST_MARKET
        return Session.CLOSED

    def _is_holiday(self, d: date) -> bool:
        return d in self._holidays
