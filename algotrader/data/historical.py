"""Historical market data service.

Fetches OHLCV data from:
  1. Upstox API (if access token is configured)
  2. CSV files (local cache / fallback)

Returns data as a dict of lists compatible with the backtesting engine.
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import date
from typing import Dict, List, Optional


def _bars_from_csv(filepath: str) -> Dict[str, List[float]]:
    """Load OHLCV bars from a CSV file with columns: date,open,high,low,close,volume."""
    bars: Dict[str, List[float]] = {
        "open": [], "high": [], "low": [], "close": [], "volume": []
    }
    with open(filepath, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bars["open"].append(float(row["open"]))
            bars["high"].append(float(row["high"]))
            bars["low"].append(float(row["low"]))
            bars["close"].append(float(row["close"]))
            bars["volume"].append(float(row.get("volume", 0)))
    return bars


def _bars_from_upstox(
    symbol: str,
    exchange: str,
    from_date: date,
    to_date: date,
    interval: str,
    access_token: str,
    sandbox: bool,
) -> Optional[Dict[str, List[float]]]:
    """Fetch historical bars from Upstox API using the upstoxlite package."""
    try:
        pkg_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "upstoxlite_package"
        )
        sys.path.insert(0, os.path.abspath(pkg_path))
        from upstoxlite import UpstoxSyncClient, UpstoxConfig  # type: ignore

        cfg = UpstoxConfig(
            client_id="", client_secret="", redirect_uri="https://localhost/", sandbox=sandbox
        )
        client = UpstoxSyncClient(cfg)
        client.access_token = access_token

        data = client.get_historical_candle_data(
            instrument_key=symbol,
            interval=interval,
            from_date=str(from_date),
            to_date=str(to_date),
        )

        bars: Dict[str, List[float]] = {
            "open": [], "high": [], "low": [], "close": [], "volume": []
        }
        candles = (data or {}).get("candles", [])
        for c in candles:
            # Upstox format: [timestamp, open, high, low, close, volume, oi]
            bars["open"].append(float(c[1]))
            bars["high"].append(float(c[2]))
            bars["low"].append(float(c[3]))
            bars["close"].append(float(c[4]))
            bars["volume"].append(float(c[5]))

        return bars if bars["close"] else None

    except Exception:
        return None


class HistoricalDataService:
    """Fetch historical OHLCV bars for backtesting."""

    def __init__(
        self,
        access_token: str = "",
        sandbox: bool = True,
        cache_dir: Optional[str] = None,
    ) -> None:
        self._access_token = access_token
        self._sandbox = sandbox
        self._cache_dir = cache_dir or os.path.join(
            os.path.dirname(__file__), "..", "..", "data_cache"
        )

    def fetch(
        self,
        symbol: str,
        exchange: str = "NSE",
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        interval: str = "1day",
    ) -> Dict[str, List[float]]:
        """Fetch bars — tries Upstox API first, falls back to CSV cache."""
        from_date = from_date or date(2020, 1, 1)
        to_date = to_date or date.today()

        # Try Upstox API
        if self._access_token:
            bars = _bars_from_upstox(
                symbol=symbol,
                exchange=exchange,
                from_date=from_date,
                to_date=to_date,
                interval=interval,
                access_token=self._access_token,
                sandbox=self._sandbox,
            )
            if bars:
                return bars

        # Try CSV cache
        csv_path = os.path.join(
            self._cache_dir, f"{exchange}_{symbol}_{interval}.csv"
        )
        if os.path.exists(csv_path):
            return _bars_from_csv(csv_path)

        raise FileNotFoundError(
            f"No historical data for {exchange}:{symbol}. "
            f"Either configure Upstox API credentials or place a CSV at {csv_path}"
        )
