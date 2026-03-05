"""Live market data feed (REST polling).

Fetches real-time OHLCV bars from the Upstox REST API.  Designed for
1-minute (or longer) interval strategies where a REST poll is sufficient.

Bar alignment rule
------------------
At time 09:31 IST, the 09:30 bar is complete.  The API returns an ordered
list of intraday candles; we take the *second-to-last* entry (index -2) as
the most recently completed bar.  The last entry is the still-forming bar.

Edge constraints
----------------
* StaleDataError raised if no successful fetch occurred in the last
  `stale_threshold_secs` seconds (default 120 s = 2 min) during market hours.
* Circuit breaker / trading halt: no ticks arrive → stale detection fires.
* WebSocket upgrade is planned but REST polling is sufficient for Phase 2.
* Reconnection: on transient API failures the caller catches the exception;
  this class retries on next call without internal sleep.
* Rate limit (429): logged as a warning; returns None for that call.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import date
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


class StaleDataError(Exception):
    """Raised when market data has not been refreshed within the expected window."""


class LiveDataFeed:
    """Polls Upstox REST API for the most recently completed OHLCV bar."""

    def __init__(
        self,
        access_token: str,
        sandbox: bool = False,
        stale_threshold_secs: int = 120,
    ) -> None:
        self._access_token = access_token
        self._sandbox = sandbox
        self._stale_threshold = stale_threshold_secs
        self._last_successful_fetch: Optional[float] = None
        self._client = self._build_client()

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_latest_bar(
        self,
        instrument_key: str,
        interval: str = "1minute",
    ) -> Optional[Dict[str, float]]:
        """Return the most recently *completed* OHLCV bar, or None on failure.

        Args:
            instrument_key: Upstox instrument key, e.g. "NSE_EQ|INE002A01018".
            interval:        Candle interval — "1minute", "5minute", "1day", etc.

        Returns:
            Dict with keys open/high/low/close/volume, or None if unavailable.
        """
        if self._client is None:
            log.warning("LiveDataFeed: no client available (upstoxlite not installed)")
            return None

        today = str(date.today())
        try:
            data = self._client.get_historical_candle_data(
                instrument_key=instrument_key,
                interval=interval,
                from_date=today,
                to_date=today,
            )
            candles = (data or {}).get("candles", [])

            # Need at least 2 entries: current forming bar + one completed bar.
            if len(candles) < 2:
                log.debug(
                    "LiveDataFeed: only %d candle(s) returned — "
                    "possibly a new session or pre-market",
                    len(candles),
                )
                return None

            # Upstox candle format: [timestamp, open, high, low, close, volume, OI]
            c = candles[-2]
            bar: Dict[str, float] = {
                "open":   float(c[1]),
                "high":   float(c[2]),
                "low":    float(c[3]),
                "close":  float(c[4]),
                "volume": float(c[5]),
            }
            self._last_successful_fetch = time.time()
            return bar

        except Exception as exc:
            status = getattr(exc, "status", None) or getattr(exc, "code", None)
            if status == 429:
                log.warning("LiveDataFeed: rate-limited (429) — will retry next bar")
            else:
                log.warning("LiveDataFeed error: %s", exc)
            return None

    def check_stale(self) -> None:
        """Raise StaleDataError if no data arrived within the stale window.

        Should be called once per bar so issues are caught promptly.
        """
        if self._last_successful_fetch is None:
            return  # Never fetched yet — not considered stale
        age = time.time() - self._last_successful_fetch
        if age > self._stale_threshold:
            raise StaleDataError(
                f"No live data received for {age:.0f}s "
                f"(threshold: {self._stale_threshold}s). "
                "Check connectivity or whether the market is halted."
            )

    @property
    def last_fetch_age_secs(self) -> Optional[float]:
        """Seconds since the last successful data fetch, or None if never fetched."""
        if self._last_successful_fetch is None:
            return None
        return time.time() - self._last_successful_fetch

    # ── Internals ──────────────────────────────────────────────────────────────

    def _build_client(self) -> Any:
        try:
            pkg_path = os.path.join(
                os.path.dirname(__file__), "..", "..", "upstoxlite_package"
            )
            sys.path.insert(0, os.path.abspath(pkg_path))
            from upstoxlite import UpstoxSyncClient, UpstoxConfig  # type: ignore

            cfg = UpstoxConfig(
                client_id="",
                client_secret="",
                redirect_uri="https://localhost/",
                sandbox=self._sandbox,
            )
            client = UpstoxSyncClient(cfg)
            client.access_token = self._access_token
            return client
        except Exception:
            return None
