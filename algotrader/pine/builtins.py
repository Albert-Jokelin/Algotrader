"""Pine Script built-in technical analysis functions (ta.* namespace).

All functions operate on Python lists (bar history) and return lists of the
same length. Values that cannot be computed (insufficient history) are
represented as float('nan').

Designed to match Pine Script v5 semantics as closely as possible.
"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple

NaN = float("nan")


def _nan_series(n: int) -> List[float]:
    return [NaN] * n


def _is_nan(v) -> bool:
    try:
        return math.isnan(v)
    except (TypeError, ValueError):
        return True


class TaLib:
    """Static methods matching Pine Script's `ta.*` namespace."""

    # ── Moving averages ───────────────────────────────────────────────────────

    @staticmethod
    def sma(series: Sequence[float], length: int) -> List[float]:
        """Simple Moving Average."""
        n = len(series)
        result = _nan_series(n)
        for i in range(length - 1, n):
            window = series[i - length + 1 : i + 1]
            if any(_is_nan(v) for v in window):
                continue
            result[i] = sum(window) / length
        return result

    @staticmethod
    def ema(series: Sequence[float], length: int) -> List[float]:
        """Exponential Moving Average (Wilder smoothing NOT used here — uses standard EMA).

        Seeded from the first SMA value.
        """
        n = len(series)
        if n < length:
            return _nan_series(n)

        result = _nan_series(n)
        k = 2.0 / (length + 1)

        # Seed with SMA
        seed = sum(series[:length]) / length
        result[length - 1] = seed

        for i in range(length, n):
            if _is_nan(series[i]):
                result[i] = result[i - 1]
            else:
                result[i] = series[i] * k + result[i - 1] * (1 - k)
        return result

    @staticmethod
    def rma(series: Sequence[float], length: int) -> List[float]:
        """Wilder Smoothing / RMA (used by RSI, ATR).

        Equivalent to EMA with α = 1/length.
        """
        n = len(series)
        if n < length:
            return _nan_series(n)

        result = _nan_series(n)
        alpha = 1.0 / length

        # Seed
        seed = sum(series[:length]) / length
        result[length - 1] = seed

        for i in range(length, n):
            if _is_nan(series[i]):
                result[i] = result[i - 1]
            else:
                result[i] = series[i] * alpha + result[i - 1] * (1 - alpha)
        return result

    # ── Oscillators ───────────────────────────────────────────────────────────

    @staticmethod
    def rsi(series: Sequence[float], length: int = 14) -> List[float]:
        """Relative Strength Index (Wilder method, matching Pine Script)."""
        n = len(series)
        result = _nan_series(n)

        # Compute gains and losses
        gains: List[float] = [NaN] * n
        losses: List[float] = [NaN] * n
        for i in range(1, n):
            delta = series[i] - series[i - 1]
            gains[i] = max(delta, 0.0)
            losses[i] = max(-delta, 0.0)

        # Seed with SMA of first `length` gains/losses
        if n <= length:
            return result

        avg_gain = sum(gains[1 : length + 1]) / length
        avg_loss = sum(losses[1 : length + 1]) / length

        if avg_loss == 0:
            result[length] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[length] = 100.0 - 100.0 / (1 + rs)

        # Wilder smoothing for subsequent bars
        for i in range(length + 1, n):
            avg_gain = (avg_gain * (length - 1) + gains[i]) / length
            avg_loss = (avg_loss * (length - 1) + losses[i]) / length
            if avg_loss == 0:
                result[i] = 100.0
            else:
                rs = avg_gain / avg_loss
                result[i] = 100.0 - 100.0 / (1 + rs)

        return result

    @staticmethod
    def macd(
        series: Sequence[float],
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> Tuple[List[float], List[float], List[float]]:
        """MACD line, signal line, and histogram."""
        fast_ema = TaLib.ema(series, fast)
        slow_ema = TaLib.ema(series, slow)

        n = len(series)
        macd_line = _nan_series(n)
        for i in range(n):
            if not (_is_nan(fast_ema[i]) or _is_nan(slow_ema[i])):
                macd_line[i] = fast_ema[i] - slow_ema[i]

        signal_line = TaLib.ema(macd_line, signal)

        histogram = _nan_series(n)
        for i in range(n):
            if not (_is_nan(macd_line[i]) or _is_nan(signal_line[i])):
                histogram[i] = macd_line[i] - signal_line[i]

        return macd_line, signal_line, histogram

    # ── Volatility ────────────────────────────────────────────────────────────

    @staticmethod
    def atr(
        high: Sequence[float],
        low: Sequence[float],
        close: Sequence[float],
        length: int = 14,
    ) -> List[float]:
        """Average True Range (Wilder smoothing)."""
        n = len(close)
        tr: List[float] = _nan_series(n)

        for i in range(1, n):
            hl = high[i] - low[i]
            hc = abs(high[i] - close[i - 1])
            lc = abs(low[i] - close[i - 1])
            tr[i] = max(hl, hc, lc)

        return TaLib.rma(tr, length)

    @staticmethod
    def bbands(
        series: Sequence[float],
        length: int = 20,
        mult: float = 2.0,
    ) -> Tuple[List[float], List[float], List[float]]:
        """Bollinger Bands: (upper, middle, lower)."""
        middle = TaLib.sma(series, length)
        std = TaLib.stdev(series, length)

        n = len(series)
        upper = _nan_series(n)
        lower = _nan_series(n)

        for i in range(n):
            if not (_is_nan(middle[i]) or _is_nan(std[i])):
                upper[i] = middle[i] + mult * std[i]
                lower[i] = middle[i] - mult * std[i]

        return upper, middle, lower

    @staticmethod
    def stdev(series: Sequence[float], length: int) -> List[float]:
        """Population standard deviation (matches Pine Script stdev)."""
        n = len(series)
        result = _nan_series(n)
        for i in range(length - 1, n):
            window = series[i - length + 1 : i + 1]
            if any(_is_nan(v) for v in window):
                continue
            mean = sum(window) / length
            variance = sum((x - mean) ** 2 for x in window) / length
            result[i] = math.sqrt(variance)
        return result

    # ── Crossover / Crossunder ────────────────────────────────────────────────

    @staticmethod
    def crossover(
        series_a: Sequence[float],
        series_b: Sequence[float],
    ) -> List[bool]:
        """Returns True on the bar where series_a crosses above series_b."""
        n = len(series_a)
        result = [False] * n
        for i in range(1, n):
            if series_a[i - 1] <= series_b[i - 1] and series_a[i] > series_b[i]:
                result[i] = True
        return result

    @staticmethod
    def crossunder(
        series_a: Sequence[float],
        series_b: Sequence[float],
    ) -> List[bool]:
        """Returns True on the bar where series_a crosses below series_b."""
        n = len(series_a)
        result = [False] * n
        for i in range(1, n):
            if series_a[i - 1] >= series_b[i - 1] and series_a[i] < series_b[i]:
                result[i] = True
        return result

    # ── Range functions ───────────────────────────────────────────────────────

    @staticmethod
    def highest(series: Sequence[float], length: int) -> List[float]:
        """Highest value over the last `length` bars."""
        n = len(series)
        result = _nan_series(n)
        for i in range(length - 1, n):
            window = series[i - length + 1 : i + 1]
            valid = [v for v in window if not _is_nan(v)]
            if valid:
                result[i] = max(valid)
        return result

    @staticmethod
    def lowest(series: Sequence[float], length: int) -> List[float]:
        """Lowest value over the last `length` bars."""
        n = len(series)
        result = _nan_series(n)
        for i in range(length - 1, n):
            window = series[i - length + 1 : i + 1]
            valid = [v for v in window if not _is_nan(v)]
            if valid:
                result[i] = min(valid)
        return result
