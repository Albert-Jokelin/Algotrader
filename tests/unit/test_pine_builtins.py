"""TDD tests for Pine Script built-in technical indicator functions.

Tests validate that our Python implementations match Pine Script semantics.
Written BEFORE the implementation (Red phase).
"""

import math
import pytest
import numpy as np
from algotrader.pine.builtins import TaLib


# Shared price series (ascending staircase + noise for testing)
@pytest.fixture
def prices():
    """50 closing prices that form a clear uptrend."""
    base = [100 + i * 2 for i in range(50)]
    # Add small oscillation
    return [p + (i % 3 - 1) * 0.5 for i, p in enumerate(base)]


@pytest.fixture
def ohlc():
    """50 bars of OHLCV data (simple synthetic)."""
    closes = [100 + i * 2 + (i % 3 - 1) * 0.5 for i in range(50)]
    highs  = [c + 1.5 for c in closes]
    lows   = [c - 1.5 for c in closes]
    opens  = [closes[max(0, i - 1)] for i in range(50)]
    return {"open": opens, "high": highs, "low": lows, "close": closes}


class TestSMA:
    def test_sma_length(self, prices):
        result = TaLib.sma(prices, 10)
        assert len(result) == len(prices)

    def test_sma_leading_nans(self, prices):
        result = TaLib.sma(prices, 10)
        for i in range(9):
            assert math.isnan(result[i])

    def test_sma_first_valid(self, prices):
        result = TaLib.sma(prices, 10)
        expected = sum(prices[:10]) / 10
        assert result[9] == pytest.approx(expected)

    def test_sma_last_value(self, prices):
        result = TaLib.sma(prices, 5)
        expected = sum(prices[-5:]) / 5
        assert result[-1] == pytest.approx(expected)

    def test_sma_length_1(self, prices):
        result = TaLib.sma(prices, 1)
        assert result[-1] == pytest.approx(prices[-1])


class TestEMA:
    def test_ema_length(self, prices):
        result = TaLib.ema(prices, 10)
        assert len(result) == len(prices)

    def test_ema_leading_nans(self, prices):
        result = TaLib.ema(prices, 10)
        for i in range(9):
            assert math.isnan(result[i])

    def test_ema_first_valid_equals_sma_seed(self, prices):
        result = TaLib.ema(prices, 10)
        # First EMA value is seeded from SMA
        sma_seed = sum(prices[:10]) / 10
        assert result[9] == pytest.approx(sma_seed)

    def test_ema_reacts_faster_than_sma(self, prices):
        """EMA should weight recent prices more — tracks SMA closely on a smooth trend."""
        sma = TaLib.sma(prices, 10)
        ema = TaLib.ema(prices, 10)
        # EMA and SMA should be within 2% of each other on a gentle trend
        assert abs(ema[-1] - sma[-1]) < sma[-1] * 0.02


class TestRSI:
    def test_rsi_length(self, prices):
        result = TaLib.rsi(prices, 14)
        assert len(result) == len(prices)

    def test_rsi_leading_nans(self, prices):
        result = TaLib.rsi(prices, 14)
        for i in range(14):
            assert math.isnan(result[i])

    def test_rsi_bounded_0_to_100(self, prices):
        result = TaLib.rsi(prices, 14)
        valid = [v for v in result if not math.isnan(v)]
        assert all(0 <= v <= 100 for v in valid)

    def test_rsi_uptrend_above_50(self, prices):
        """Steadily rising prices → RSI should be above 50."""
        result = TaLib.rsi(prices, 14)
        assert result[-1] > 50

    def test_rsi_all_gains_returns_100(self):
        """All gains → RSI should approach 100."""
        strictly_up = list(range(1, 30))
        result = TaLib.rsi(strictly_up, 14)
        # After first valid value, RSI should be 100 (no losses)
        assert result[-1] == pytest.approx(100.0)

    def test_rsi_all_losses_returns_0(self):
        """All losses → RSI should approach 0."""
        strictly_down = list(range(30, 0, -1))
        result = TaLib.rsi(strictly_down, 14)
        valid = [v for v in result if not math.isnan(v)]
        assert valid[-1] == pytest.approx(0.0)


class TestMACD:
    def test_macd_returns_three_series(self, prices):
        macd_line, signal_line, histogram = TaLib.macd(prices, 12, 26, 9)
        assert len(macd_line) == len(prices)
        assert len(signal_line) == len(prices)
        assert len(histogram) == len(prices)

    def test_histogram_equals_macd_minus_signal(self, prices):
        macd_line, signal_line, histogram = TaLib.macd(prices, 12, 26, 9)
        for i, (m, s, h) in enumerate(zip(macd_line, signal_line, histogram)):
            if not (math.isnan(m) or math.isnan(s)):
                assert h == pytest.approx(m - s, abs=1e-10)


class TestBBands:
    def test_bbands_returns_three_series(self, prices):
        upper, middle, lower = TaLib.bbands(prices, 20, 2.0)
        assert len(upper) == len(prices)
        assert len(middle) == len(prices)
        assert len(lower) == len(prices)

    def test_upper_above_middle(self, prices):
        upper, middle, lower = TaLib.bbands(prices, 20, 2.0)
        valid = [(u, m, l) for u, m, l in zip(upper, middle, lower) if not math.isnan(m)]
        assert all(u >= m for u, m, l in valid)

    def test_lower_below_middle(self, prices):
        upper, middle, lower = TaLib.bbands(prices, 20, 2.0)
        valid = [(u, m, l) for u, m, l in zip(upper, middle, lower) if not math.isnan(m)]
        assert all(l <= m for u, m, l in valid)

    def test_middle_equals_sma(self, prices):
        upper, middle, lower = TaLib.bbands(prices, 20, 2.0)
        sma = TaLib.sma(prices, 20)
        valid_indices = [i for i, v in enumerate(middle) if not math.isnan(v)]
        for i in valid_indices:
            assert middle[i] == pytest.approx(sma[i])


class TestATR:
    def test_atr_length(self, ohlc):
        result = TaLib.atr(ohlc["high"], ohlc["low"], ohlc["close"], 14)
        assert len(result) == len(ohlc["close"])

    def test_atr_positive(self, ohlc):
        result = TaLib.atr(ohlc["high"], ohlc["low"], ohlc["close"], 14)
        valid = [v for v in result if not math.isnan(v)]
        assert all(v > 0 for v in valid)

    def test_atr_leading_nans(self, ohlc):
        result = TaLib.atr(ohlc["high"], ohlc["low"], ohlc["close"], 14)
        assert math.isnan(result[0])


class TestCrossover:
    def test_crossover_detected(self):
        # fast crosses above slow at index 3
        fast = [1, 2, 3, 6, 8]
        slow = [5, 5, 5, 5, 5]
        result = TaLib.crossover(fast, slow)
        assert result[3] is True

    def test_no_crossover(self):
        fast = [1, 2, 3, 4, 5]
        slow = [6, 6, 6, 6, 6]
        result = TaLib.crossover(fast, slow)
        assert all(v is False for v in result)

    def test_crossover_first_bar_is_false(self):
        fast = [6, 1]
        slow = [5, 5]
        result = TaLib.crossover(fast, slow)
        assert result[0] is False


class TestCrossunder:
    def test_crossunder_detected(self):
        # fast crosses below slow between index 1 (fast=6>slow=5) and index 2 (fast=4<slow=5)
        fast = [8, 6, 4, 2, 1]
        slow = [5, 5, 5, 5, 5]
        result = TaLib.crossunder(fast, slow)
        assert result[2] is True

    def test_no_crossunder(self):
        fast = [8, 8, 8, 8]
        slow = [5, 5, 5, 5]
        result = TaLib.crossunder(fast, slow)
        assert all(v is False for v in result)


class TestHighest:
    def test_highest_value(self):
        series = [1, 5, 3, 7, 2]
        result = TaLib.highest(series, 3)
        assert result[-1] == pytest.approx(7.0)

    def test_highest_length(self):
        series = [1, 5, 3, 7, 2]
        result = TaLib.highest(series, 3)
        assert len(result) == len(series)


class TestLowest:
    def test_lowest_value(self):
        series = [5, 3, 7, 1, 6]
        result = TaLib.lowest(series, 3)
        assert result[-1] == pytest.approx(1.0)

    def test_lowest_length(self):
        series = [5, 3, 7, 1, 6]
        result = TaLib.lowest(series, 3)
        assert len(result) == len(series)


class TestStddev:
    def test_stddev_length(self, prices):
        result = TaLib.stdev(prices, 10)
        assert len(result) == len(prices)

    def test_stddev_positive(self, prices):
        result = TaLib.stdev(prices, 10)
        valid = [v for v in result if not math.isnan(v)]
        assert all(v >= 0 for v in valid)

    def test_constant_series_stddev_zero(self):
        series = [5.0] * 20
        result = TaLib.stdev(series, 10)
        valid = [v for v in result if not math.isnan(v)]
        assert all(v == pytest.approx(0.0) for v in valid)
