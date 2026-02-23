"""TDD tests for Pine Script evaluator (interpreter).

The evaluator runs a parsed Pine Script AST bar-by-bar against OHLCV data,
collecting strategy.entry/exit/close calls as TradingSignal objects.

Written BEFORE the implementation (Red phase).
"""

import math
import pytest

from algotrader.pine.evaluator import Evaluator, EvaluatorError
from algotrader.pine.lexer import Lexer
from algotrader.pine.parser import Parser
from algotrader.signals.models import SignalAction


# ── Helper ────────────────────────────────────────────────────────────────────

def run_script(pine_src: str, bars: dict) -> list:
    """Lex → parse → evaluate a Pine Script and return all emitted signals."""
    tokens = Lexer(pine_src).tokenize()
    ast = Parser(tokens).parse()
    ev = Evaluator(bars)
    return ev.run(ast)


def make_bars(n=30, start=100.0, step=1.0):
    """Generate n ascending OHLCV bars."""
    closes = [start + i * step for i in range(n)]
    return {
        "open":   [c - 0.5 for c in closes],
        "high":   [c + 1.0 for c in closes],
        "low":    [c - 1.0 for c in closes],
        "close":  closes,
        "volume": [10000.0] * n,
    }


# ── Evaluator basics ──────────────────────────────────────────────────────────

class TestEvaluatorBasics:
    def test_runs_without_error_on_empty_script(self):
        signals = run_script("", make_bars())
        assert isinstance(signals, list)

    def test_scalar_assignment(self):
        ev = Evaluator(make_bars())
        tokens = Lexer("x = 42").tokenize()
        ast = Parser(tokens).parse()
        ev.run(ast)
        assert ev.get_var("x") == 42

    def test_float_assignment(self):
        ev = Evaluator(make_bars())
        tokens = Lexer("x = 3.14").tokenize()
        ast = Parser(tokens).parse()
        ev.run(ast)
        assert ev.get_var("x") == pytest.approx(3.14)

    def test_string_assignment(self):
        ev = Evaluator(make_bars())
        tokens = Lexer('x = "hello"').tokenize()
        ast = Parser(tokens).parse()
        ev.run(ast)
        assert ev.get_var("x") == "hello"

    def test_true_literal(self):
        ev = Evaluator(make_bars())
        tokens = Lexer("x = true").tokenize()
        ast = Parser(tokens).parse()
        ev.run(ast)
        assert ev.get_var("x") is True

    def test_false_literal(self):
        ev = Evaluator(make_bars())
        tokens = Lexer("x = false").tokenize()
        ast = Parser(tokens).parse()
        ev.run(ast)
        assert ev.get_var("x") is False

    def test_na_literal(self):
        ev = Evaluator(make_bars())
        tokens = Lexer("x = na").tokenize()
        ast = Parser(tokens).parse()
        ev.run(ast)
        assert ev.get_var("x") is None


# ── Built-in series ───────────────────────────────────────────────────────────

class TestBuiltinSeries:
    def test_close_resolves_to_last_bar(self):
        bars = make_bars(n=10, start=100.0, step=1.0)
        ev = Evaluator(bars)
        tokens = Lexer("x = close").tokenize()
        ast = Parser(tokens).parse()
        ev.run(ast)
        # After running all 10 bars, `close` on last bar is 109.0
        assert ev.get_var("x") == pytest.approx(109.0)

    def test_high_series(self):
        bars = make_bars(n=5)
        ev = Evaluator(bars)
        tokens = Lexer("x = high").tokenize()
        ast = Parser(tokens).parse()
        ev.run(ast)
        assert ev.get_var("x") == pytest.approx(bars["high"][-1])

    def test_series_prev_bar_access(self):
        bars = make_bars(n=10, start=100.0, step=1.0)
        ev = Evaluator(bars)
        tokens = Lexer("x = close[1]").tokenize()
        ast = Parser(tokens).parse()
        ev.run(ast)
        # close[1] on last bar = close of second-to-last bar
        assert ev.get_var("x") == pytest.approx(bars["close"][-2])


# ── Arithmetic ────────────────────────────────────────────────────────────────

class TestArithmetic:
    def test_addition(self):
        ev = Evaluator(make_bars())
        tokens = Lexer("x = 3 + 4").tokenize()
        ast = Parser(tokens).parse()
        ev.run(ast)
        assert ev.get_var("x") == pytest.approx(7)

    def test_subtraction(self):
        ev = Evaluator(make_bars())
        tokens = Lexer("x = 10 - 3").tokenize()
        ast = Parser(tokens).parse()
        ev.run(ast)
        assert ev.get_var("x") == pytest.approx(7)

    def test_multiplication(self):
        ev = Evaluator(make_bars())
        tokens = Lexer("x = 3 * 4").tokenize()
        ast = Parser(tokens).parse()
        ev.run(ast)
        assert ev.get_var("x") == pytest.approx(12)

    def test_division(self):
        ev = Evaluator(make_bars())
        tokens = Lexer("x = 10 / 4").tokenize()
        ast = Parser(tokens).parse()
        ev.run(ast)
        assert ev.get_var("x") == pytest.approx(2.5)


# ── Comparisons ───────────────────────────────────────────────────────────────

class TestComparisons:
    def test_greater_than_true(self):
        ev = Evaluator(make_bars())
        tokens = Lexer("x = 5 > 3").tokenize()
        ast = Parser(tokens).parse()
        ev.run(ast)
        assert ev.get_var("x") is True

    def test_greater_than_false(self):
        ev = Evaluator(make_bars())
        tokens = Lexer("x = 3 > 5").tokenize()
        ast = Parser(tokens).parse()
        ev.run(ast)
        assert ev.get_var("x") is False

    def test_equals_true(self):
        ev = Evaluator(make_bars())
        tokens = Lexer("x = 5 == 5").tokenize()
        ast = Parser(tokens).parse()
        ev.run(ast)
        assert ev.get_var("x") is True


# ── ta.* function calls ───────────────────────────────────────────────────────

class TestTaFunctions:
    def test_ta_sma(self):
        bars = make_bars(n=30)
        ev = Evaluator(bars)
        tokens = Lexer("x = ta.sma(close, 10)").tokenize()
        ast = Parser(tokens).parse()
        ev.run(ast)
        x = ev.get_var("x")
        assert not math.isnan(x)

    def test_ta_ema(self):
        bars = make_bars(n=30)
        ev = Evaluator(bars)
        tokens = Lexer("x = ta.ema(close, 10)").tokenize()
        ast = Parser(tokens).parse()
        ev.run(ast)
        x = ev.get_var("x")
        assert not math.isnan(x)

    def test_ta_rsi(self):
        bars = make_bars(n=30)
        ev = Evaluator(bars)
        tokens = Lexer("x = ta.rsi(close, 14)").tokenize()
        ast = Parser(tokens).parse()
        ev.run(ast)
        x = ev.get_var("x")
        assert not math.isnan(x)
        assert 0 <= x <= 100

    def test_ta_crossover(self):
        bars = make_bars(n=30)
        ev = Evaluator(bars)
        src = "x = ta.crossover(ta.ema(close, 5), ta.ema(close, 20))"
        tokens = Lexer(src).tokenize()
        ast = Parser(tokens).parse()
        ev.run(ast)
        x = ev.get_var("x")
        assert isinstance(x, bool)


# ── strategy.entry signals ────────────────────────────────────────────────────

class TestStrategySignals:
    def test_strategy_entry_emits_signal(self):
        bars = make_bars(n=5)
        src = 'strategy("Test", overlay=true)\nstrategy.entry("Long", strategy.long)'
        signals = run_script(src, bars)
        buy_signals = [s for s in signals if s.action == SignalAction.BUY]
        assert len(buy_signals) > 0

    def test_strategy_entry_long_sets_action_buy(self):
        bars = make_bars(n=3)
        src = 'strategy.entry("L", strategy.long)'
        signals = run_script(src, bars)
        assert all(s.action == SignalAction.BUY for s in signals)

    def test_strategy_entry_short_sets_action_sell(self):
        bars = make_bars(n=3)
        src = 'strategy.entry("S", strategy.short)'
        signals = run_script(src, bars)
        assert all(s.action == SignalAction.SELL for s in signals)

    def test_strategy_close_emits_exit_signal(self):
        bars = make_bars(n=3)
        src = 'strategy.close("Long")'
        signals = run_script(src, bars)
        exit_signals = [s for s in signals if s.is_exit]
        assert len(exit_signals) > 0

    def test_if_condition_gates_signal(self):
        """strategy.entry inside a false condition should NOT emit a signal."""
        bars = make_bars(n=5)
        src = 'if false\n    strategy.entry("L", strategy.long)'
        signals = run_script(src, bars)
        assert len(signals) == 0

    def test_conditional_entry_on_true(self):
        bars = make_bars(n=5)
        src = 'if true\n    strategy.entry("L", strategy.long)'
        signals = run_script(src, bars)
        assert len(signals) > 0

    def test_ema_crossover_strategy(self):
        """A realistic EMA crossover script produces signals."""
        bars = make_bars(n=60, start=100.0, step=0.5)
        src = (
            'strategy("EMA Cross", overlay=true)\n'
            'fast = ta.ema(close, 5)\n'
            'slow = ta.ema(close, 20)\n'
            'if ta.crossover(fast, slow)\n'
            '    strategy.entry("Long", strategy.long)\n'
            'if ta.crossunder(fast, slow)\n'
            '    strategy.close("Long")'
        )
        signals = run_script(src, bars)
        # On a monotonically rising series fast EMA is always above slow
        # so we may get 0 crossovers — just verify no errors and returns list
        assert isinstance(signals, list)
