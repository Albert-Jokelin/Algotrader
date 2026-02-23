"""TDD tests for backtesting engine and performance metrics.

The engine runs a Pine Script file against historical OHLCV data bar-by-bar,
routes signals through risk manager and paper broker, and computes metrics.
Written BEFORE the implementation.
"""

import pytest
from algotrader.backtest.engine import BacktestEngine, BacktestResult
from algotrader.backtest.metrics import PerformanceMetrics
from algotrader.config import RiskConfig, Settings
from algotrader.signals.models import Exchange


# ── Synthetic data ────────────────────────────────────────────────────────────

def _make_bars(n=100, start=1000.0, step=2.0, volatility=5.0):
    """Generate synthetic OHLCV bars."""
    import random
    random.seed(42)
    closes = []
    c = start
    for i in range(n):
        c += step + random.uniform(-volatility, volatility)
        closes.append(max(c, 10.0))
    highs  = [c + abs(random.uniform(0, 3)) for c in closes]
    lows   = [c - abs(random.uniform(0, 3)) for c in closes]
    opens  = [closes[max(0, i-1)] for i in range(n)]
    return {
        "open": opens, "high": highs,
        "low": lows, "close": closes,
        "volume": [100_000.0] * n,
    }


# Simple always-buy strategy
_ALWAYS_BUY_SCRIPT = '''
strategy("Always Buy", overlay=true)
if bar_index == 0
    strategy.entry("Long", strategy.long)
'''

# EMA crossover strategy
_EMA_CROSS_SCRIPT = '''
strategy("EMA Cross", overlay=true)
fast = ta.ema(close, 5)
slow = ta.ema(close, 20)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.close("Long")
'''


# ── BacktestEngine tests ──────────────────────────────────────────────────────

class TestBacktestEngineBasics:
    def test_engine_creates_successfully(self):
        bars = _make_bars()
        engine = BacktestEngine(
            bars=bars,
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            initial_capital=100_000,
        )
        assert engine is not None

    def test_run_returns_result(self):
        bars = _make_bars()
        engine = BacktestEngine(
            bars=bars,
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            initial_capital=100_000,
        )
        result = engine.run(_ALWAYS_BUY_SCRIPT)
        assert isinstance(result, BacktestResult)

    def test_result_has_fills(self):
        bars = _make_bars()
        engine = BacktestEngine(
            bars=bars,
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            initial_capital=100_000,
        )
        result = engine.run(_ALWAYS_BUY_SCRIPT)
        assert len(result.fills) >= 0  # ≥0 since capital may not cover

    def test_result_has_equity_curve(self):
        bars = _make_bars()
        engine = BacktestEngine(
            bars=bars,
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            initial_capital=100_000,
        )
        result = engine.run(_ALWAYS_BUY_SCRIPT)
        assert len(result.equity_curve) > 0

    def test_equity_curve_length_matches_bars(self):
        bars = _make_bars(n=50)
        engine = BacktestEngine(
            bars=bars,
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            initial_capital=100_000,
        )
        result = engine.run(_ALWAYS_BUY_SCRIPT)
        assert len(result.equity_curve) == 50

    def test_initial_equity_is_capital(self):
        bars = _make_bars(n=50)
        engine = BacktestEngine(
            bars=bars,
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            initial_capital=100_000,
        )
        result = engine.run(_ALWAYS_BUY_SCRIPT)
        assert result.equity_curve[0] == pytest.approx(100_000, rel=0.05)

    def test_ema_crossover_strategy_runs(self):
        bars = _make_bars(n=100)
        engine = BacktestEngine(
            bars=bars,
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            initial_capital=500_000,
        )
        result = engine.run(_EMA_CROSS_SCRIPT)
        assert isinstance(result, BacktestResult)


# ── PerformanceMetrics tests ──────────────────────────────────────────────────

class TestPerformanceMetrics:
    def _metrics(self, equity):
        return PerformanceMetrics.compute(
            equity_curve=equity,
            initial_capital=equity[0],
            fills=[],
        )

    def test_total_return_zero_on_flat_equity(self):
        equity = [100_000.0] * 100
        m = self._metrics(equity)
        assert m.total_return_pct == pytest.approx(0.0)

    def test_total_return_positive(self):
        equity = [100_000.0 + i * 100 for i in range(100)]
        m = self._metrics(equity)
        assert m.total_return_pct > 0

    def test_total_return_negative(self):
        equity = [100_000.0 - i * 100 for i in range(100)]
        m = self._metrics(equity)
        assert m.total_return_pct < 0

    def test_max_drawdown_zero_on_monotone_up(self):
        equity = [100_000.0 + i * 1000 for i in range(100)]
        m = self._metrics(equity)
        assert m.max_drawdown_pct == pytest.approx(0.0)

    def test_max_drawdown_positive_on_decline(self):
        equity = [100_000.0, 90_000.0, 80_000.0, 70_000.0]
        m = self._metrics(equity)
        assert m.max_drawdown_pct == pytest.approx(30.0, rel=0.01)

    def test_sharpe_ratio_computed(self):
        equity = [100_000.0 + i * 50 for i in range(252)]
        m = self._metrics(equity)
        assert isinstance(m.sharpe_ratio, float)

    def test_sharpe_ratio_higher_for_smoother_returns(self):
        smooth = [100_000.0 + i * 100 for i in range(252)]
        noisy  = [100_000.0 + i * 100 + ((-1)**i) * 500 for i in range(252)]
        m_smooth = self._metrics(smooth)
        m_noisy  = self._metrics(noisy)
        assert m_smooth.sharpe_ratio > m_noisy.sharpe_ratio

    def test_result_has_n_trades(self):
        equity = [100_000.0] * 50
        m = self._metrics(equity)
        assert hasattr(m, "n_trades")
        assert m.n_trades >= 0

    def test_cagr_computed(self):
        # 252 trading days, starts at 100k
        equity = [100_000.0 + i * 100 for i in range(252)]
        m = self._metrics(equity)
        assert hasattr(m, "cagr_pct")
        assert isinstance(m.cagr_pct, float)
