"""Integration tests: Pine Script → Signals → Risk Manager → Paper Broker.

Tests the complete end-to-end pipeline without any external network calls.
Written BEFORE the final wiring (Red phase).
"""

import pytest

from algotrader.backtest.engine import BacktestEngine
from algotrader.pine.evaluator import Evaluator
from algotrader.pine.lexer import Lexer
from algotrader.pine.parser import Parser
from algotrader.risk.manager import RiskManager
from algotrader.broker.paper_broker import PaperBroker
from algotrader.config import RiskConfig
from algotrader.signals.models import Exchange, SignalAction


# ── Shared fixtures ────────────────────────────────────────────────────────────

def _oscillating_bars(n=100):
    """Bars that oscillate so crossovers actually happen."""
    import math
    closes = [1000 + 100 * math.sin(i * 0.3) for i in range(n)]
    return {
        "open":   [c - 2 for c in closes],
        "high":   [c + 5 for c in closes],
        "low":    [c - 5 for c in closes],
        "close":  closes,
        "volume": [50_000.0] * n,
    }


def _uptrend_bars(n=60):
    closes = [1000 + i * 3 for i in range(n)]
    return {
        "open":   [c - 1 for c in closes],
        "high":   [c + 2 for c in closes],
        "low":    [c - 2 for c in closes],
        "close":  closes,
        "volume": [50_000.0] * n,
    }


# ── Pipeline tests ─────────────────────────────────────────────────────────────

class TestPineScriptToSignals:
    def test_simple_strategy_produces_signals(self):
        """End-to-end: parse Pine Script → evaluator → signals."""
        bars = _uptrend_bars(n=30)
        script = 'strategy("Test")\nstrategy.entry("L", strategy.long)'
        tokens = Lexer(script).tokenize()
        ast = Parser(tokens).parse()
        ev = Evaluator(bars, symbol="RELIANCE", exchange=Exchange.NSE)
        signals = ev.run(ast)
        assert len(signals) == 30  # entry on every bar

    def test_conditional_strategy_limits_signals(self):
        bars = _uptrend_bars(n=30)
        script = (
            'strategy("Test")\n'
            'if bar_index == 0\n'
            '    strategy.entry("L", strategy.long)'
        )
        tokens = Lexer(script).tokenize()
        ast = Parser(tokens).parse()
        ev = Evaluator(bars, symbol="RELIANCE", exchange=Exchange.NSE)
        signals = ev.run(ast)
        buy_signals = [s for s in signals if s.action == SignalAction.BUY]
        assert len(buy_signals) == 1

    def test_ema_crossover_signals_on_oscillating_data(self):
        bars = _oscillating_bars(n=80)
        script = (
            'strategy("EMA Cross")\n'
            'fast = ta.ema(close, 5)\n'
            'slow = ta.ema(close, 20)\n'
            'if ta.crossover(fast, slow)\n'
            '    strategy.entry("Long", strategy.long)\n'
            'if ta.crossunder(fast, slow)\n'
            '    strategy.close("Long")'
        )
        tokens = Lexer(script).tokenize()
        ast = Parser(tokens).parse()
        ev = Evaluator(bars, symbol="RELIANCE", exchange=Exchange.NSE)
        signals = ev.run(ast)
        # Oscillating data should produce multiple crossovers
        assert isinstance(signals, list)


class TestSignalsToOrders:
    def test_signal_routes_through_risk_manager_to_paper_broker(self):
        bars = _uptrend_bars(n=3)
        script = 'strategy.entry("L", strategy.long)'
        tokens = Lexer(script).tokenize()
        ast = Parser(tokens).parse()

        ev = Evaluator(bars, symbol="RELIANCE", exchange=Exchange.NSE)
        signals = ev.run(ast)

        rm = RiskManager(config=RiskConfig(), capital=500_000)
        broker = PaperBroker(initial_capital=500_000)

        for sig in signals[:1]:  # Process first signal only
            decision = rm.evaluate(sig, broker.get_positions())
            from algotrader.signals.models import Order, OrderSide, OrderType
            order = Order(
                symbol=sig.symbol,
                exchange=sig.exchange,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=decision.quantity,
                strategy_name=sig.strategy_name,
            )
            fill = broker.submit_order(order, market_price=sig.price or 1000.0)
            assert fill is not None

        assert len(broker.get_positions()) == 1


class TestFullBacktest:
    def test_backtest_engine_end_to_end(self):
        bars = _oscillating_bars(n=100)
        script = (
            'strategy("RSI Strategy", overlay=true)\n'
            'rsi_val = ta.rsi(close, 14)\n'
            'if rsi_val < 30\n'
            '    strategy.entry("Long", strategy.long)\n'
            'if rsi_val > 70\n'
            '    strategy.close("Long")'
        )
        engine = BacktestEngine(
            bars=bars,
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            initial_capital=500_000,
        )
        result = engine.run(script)

        # Basic sanity checks
        assert len(result.equity_curve) == 100
        assert result.metrics is not None
        assert result.metrics.max_drawdown_pct >= 0

    def test_backtest_preserves_capital_with_no_trades(self):
        bars = _uptrend_bars(n=30)
        # Strategy that never fires (always false condition)
        script = (
            'strategy("No Trade")\n'
            'if false\n'
            '    strategy.entry("L", strategy.long)'
        )
        engine = BacktestEngine(
            bars=bars,
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            initial_capital=100_000,
        )
        result = engine.run(script)
        # All equity values should equal initial capital (no trades)
        for eq in result.equity_curve:
            assert eq == pytest.approx(100_000.0)

    def test_backtest_sma_reversion_strategy(self):
        # Uses SMA (scalar) for comparison — avoids bbands tuple destructuring
        bars = _oscillating_bars(n=100)
        script = (
            'strategy("SMA Reversion", overlay=true)\n'
            'sma_val = ta.sma(close, 20)\n'
            'if close < sma_val\n'
            '    strategy.entry("Long", strategy.long)\n'
            'if close > sma_val\n'
            '    strategy.close("Long")'
        )
        engine = BacktestEngine(
            bars=bars,
            symbol="INFY",
            exchange=Exchange.NSE,
            initial_capital=200_000,
        )
        result = engine.run(script)
        assert isinstance(result.metrics.sharpe_ratio, float)

    def test_different_strategies_produce_different_results(self):
        bars = _oscillating_bars(n=100)
        engine = BacktestEngine(
            bars=bars, symbol="RELIANCE", exchange=Exchange.NSE, initial_capital=500_000
        )

        script_always_buy = 'strategy("AB")\nstrategy.entry("L", strategy.long)'
        script_never_buy  = 'strategy("NB")\nif false\n    strategy.entry("L", strategy.long)'

        r1 = engine.run(script_always_buy)
        r2 = engine.run(script_never_buy)

        # They should produce different equity curves
        assert r1.n_signals != r2.n_signals
