"""Upstox instrument master — downloads, caches, and serves instrument keys.

The Upstox API requires an instrument_key (e.g. "NSE_EQ|INE002A01018") for
every order and market-data request.  This module fetches the daily CSV from
Upstox's CDN, caches it, and provides O(1) lookup by (trading_symbol, exchange).

Edge constraints respected
--------------------------
* File is only re-downloaded when the cache is older than STALE_HOURS.
* If the download fails and a cache exists, the stale cache is used with a warning.
* If neither is available, a hardcoded fallback map is used for major NSE stocks.
* Only EQ (equity) instruments are loaded; NFO/MCX are filtered out.
* The CSV is large (~30 MB uncompressed). It is streamed / decompressed in memory,
  then written as plain CSV so subsequent reads are fast.
* Lookup raises InstrumentNotFoundError with a clear remediation message.
"""

from __future__ import annotations

import csv
import gzip
import io
import logging
import os
import time
from typing import Dict, Optional, Tuple

log = logging.getLogger(__name__)

# CDN URL — Upstox publishes one per exchange, updated before market open.
_URL_TEMPLATE = (
    "https://assets.upstox.com/market-quote/instruments/exchange/{exchange}.csv.gz"
)

# Re-download if cache file is older than this many hours.
_STALE_HOURS = 20

# Hardcoded fallback for the most-traded NSE equities
# (used when both download and cache are unavailable).
_FALLBACK: Dict[Tuple[str, str], str] = {
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


class InstrumentMaster:
    """Instrument key lookup for Upstox API calls."""

    def __init__(
        self,
        cache_dir: str = "~/.algotrader/instruments",
        exchange: str = "NSE",
    ) -> None:
        self._cache_dir = os.path.expanduser(cache_dir)
        self._exchange = exchange.upper()
        # (trading_symbol, exchange) → instrument_key
        self._map: Dict[Tuple[str, str], str] = {}
        self._loaded = False

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_instrument_key(self, symbol: str, exchange: str) -> str:
        """Return the Upstox instrument key for a given symbol + exchange.

        Raises:
            algotrader.exceptions.InstrumentNotFoundError if not found.
        """
        if not self._loaded:
            self.load()

        key = (symbol.upper(), exchange.upper())
        if key in self._map:
            return self._map[key]

        if key in _FALLBACK:
            return _FALLBACK[key]

        from algotrader.exceptions import InstrumentNotFoundError
        raise InstrumentNotFoundError(
            f"No instrument key for {exchange}:{symbol}. "
            "Run 'python -m algotrader update-instruments' to refresh the cache."
        )

    def load(self, force: bool = False) -> None:
        """Load instruments from cache (or download if stale / missing)."""
        cache_path = self._cache_path()

        if not force and os.path.exists(cache_path):
            age_secs = time.time() - os.path.getmtime(cache_path)
            if age_secs < _STALE_HOURS * 3600:
                self._parse_file(cache_path)
                return

        downloaded = self._download(cache_path)
        if downloaded:
            self._parse_file(cache_path)
        elif os.path.exists(cache_path):
            log.warning(
                "Instruments download failed; using stale cache: %s", cache_path
            )
            self._parse_file(cache_path)
        else:
            log.warning(
                "Instruments download failed and no cache found; "
                "using hardcoded fallback for %d symbols",
                len(_FALLBACK),
            )
            self._map.update(_FALLBACK)
            self._loaded = True

    @classmethod
    def from_csv_text(cls, csv_text: str, exchange: str = "NSE") -> "InstrumentMaster":
        """Build an InstrumentMaster directly from CSV text (for testing)."""
        obj = cls.__new__(cls)
        obj._cache_dir = ""
        obj._exchange = exchange.upper()
        obj._map = {}
        obj._loaded = True
        obj._parse_text(csv_text)
        return obj

    @property
    def size(self) -> int:
        """Number of equity instruments loaded."""
        return len(self._map)

    # ── Internals ──────────────────────────────────────────────────────────────

    def _cache_path(self) -> str:
        os.makedirs(self._cache_dir, exist_ok=True)
        return os.path.join(self._cache_dir, f"{self._exchange}_instruments.csv")

    def _download(self, cache_path: str) -> bool:
        """Download and decompress the instrument CSV; return True on success."""
        url = _URL_TEMPLATE.format(exchange=self._exchange)
        try:
            import urllib.request
            log.info("Downloading instrument master: %s", url)
            with urllib.request.urlopen(url, timeout=30) as resp:
                compressed = resp.read()
            data = gzip.decompress(compressed)
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "wb") as fh:
                fh.write(data)
            log.info("Instrument master saved (%d bytes uncompressed)", len(data))
            return True
        except Exception as exc:
            log.warning("Could not download instrument master: %s", exc)
            return False

    def _parse_file(self, path: str) -> None:
        with open(path, newline="", encoding="utf-8") as fh:
            self._parse_text(fh.read())

    def _parse_text(self, text: str) -> None:
        """Parse Upstox CSV format and populate self._map."""
        reader = csv.DictReader(io.StringIO(text))
        count = 0
        for row in reader:
            if row.get("instrument_type", "").upper() != "EQ":
                continue
            symbol = row.get("tradingsymbol", "").upper()
            exch   = row.get("exchange", "").upper()
            ikey   = row.get("instrument_key", "").strip()
            if symbol and exch and ikey:
                self._map[(symbol, exch)] = ikey
                count += 1
        self._loaded = True
        log.debug("InstrumentMaster: loaded %d equity instruments", count)
