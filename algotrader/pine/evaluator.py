"""Pine Script v5 interpreter / evaluator.

Runs a parsed Pine Script AST bar-by-bar against OHLCV data.
Collects strategy.entry / strategy.exit / strategy.close calls
and converts them to TradingSignal objects.

The evaluator maintains:
  - A bar context (current bar index, OHLCV values)
  - A variable environment (symbols → values)
  - A series store (symbol → full history list, for series indexing)
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

from algotrader.pine.ast_nodes import (
    AssignStatement,
    BinaryOp,
    FunctionCall,
    Identifier,
    IfStatement,
    IndicatorDeclaration,
    Literal,
    MemberAccess,
    Node,
    Program,
    SeriesIndex,
    StrategyClose,
    StrategyDeclaration,
    StrategyEntry,
    StrategyExit,
    UnaryOp,
    VarDeclaration,
)
from algotrader.pine.builtins import TaLib
from algotrader.signals.models import (
    Exchange,
    SignalAction,
    SignalSource,
    TradingSignal,
)

NaN = float("nan")


class EvaluatorError(Exception):
    pass


class Evaluator:
    """Bar-by-bar interpreter for a Pine Script AST."""

    # Built-in series names that resolve from bar data
    _OHLCV = frozenset({"open", "high", "low", "close", "volume"})

    def __init__(
        self,
        bars: Dict[str, List[float]],
        symbol: str = "UNKNOWN",
        exchange: Exchange = Exchange.NSE,
        strategy_name: str = "pine_strategy",
    ) -> None:
        self._bars = bars
        self._symbol = symbol
        self._exchange = exchange
        self._strategy_name = strategy_name

        # Number of bars
        self._n_bars = len(bars["close"])

        # Current bar index (updated in _run_bar)
        self._bar_index = 0

        # Variable environment: name → current value
        self._env: Dict[str, Any] = {}

        # Persistent var storage (survives bar-to-bar)
        self._persistent: Dict[str, Any] = {}

        # Series history: name → list of values (one per bar)
        self._series: Dict[str, List[Any]] = {}

        # Emitted signals
        self._signals: List[TradingSignal] = []

        # Precompute full-series ta.* values so we can look them up efficiently
        self._precomputed: Dict[str, Any] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(self, program: Program) -> List[TradingSignal]:
        """Execute the program bar-by-bar and return all emitted signals."""
        self._signals = []

        for bar_idx in range(self._n_bars):
            self._bar_index = bar_idx
            # Reset per-bar env (but keep persistent vars)
            self._env = dict(self._persistent)
            self._env["bar_index"] = bar_idx

            # Execute all top-level statements for this bar
            for stmt in program.body:
                self._exec(stmt)

            # Save updated persistent vars back
            for k, v in self._env.items():
                if k in self._persistent:
                    self._persistent[k] = v

        return self._signals

    def get_var(self, name: str) -> Any:
        """Return the current value of a variable (for testing)."""
        return self._env.get(name, self._persistent.get(name))

    # ── Statement execution ────────────────────────────────────────────────────

    def _exec(self, node: Node) -> None:
        if isinstance(node, AssignStatement):
            val = self._eval(node.value)
            self._env[node.name] = val
            # Track series history
            if node.name not in self._series:
                self._series[node.name] = []
            # Pad with NaN if bars were skipped
            while len(self._series[node.name]) < self._bar_index:
                self._series[node.name].append(NaN)
            self._series[node.name].append(val)

        elif isinstance(node, VarDeclaration):
            # var = persistent; only set on first bar if not already set
            if node.name not in self._persistent:
                val = self._eval(node.value)
                self._persistent[node.name] = val
            self._env[node.name] = self._persistent[node.name]

        elif isinstance(node, IfStatement):
            cond = self._eval(node.condition)
            if self._truthy(cond):
                for stmt in node.body:
                    self._exec(stmt)
            elif node.else_body:
                for stmt in node.else_body:
                    self._exec(stmt)

        elif isinstance(node, StrategyDeclaration):
            if node.title:
                self._strategy_name = node.title

        elif isinstance(node, IndicatorDeclaration):
            pass  # Indicators don't trade

        elif isinstance(node, StrategyEntry):
            self._emit_entry(node)

        elif isinstance(node, StrategyExit):
            self._emit_exit(node)

        elif isinstance(node, StrategyClose):
            self._emit_close(node)

        elif isinstance(node, (FunctionCall, BinaryOp, Identifier, Literal, MemberAccess)):
            # Expression used as statement (side-effect evaluation)
            self._eval(node)

    # ── Expression evaluation ──────────────────────────────────────────────────

    def _eval(self, node: Node) -> Any:
        if isinstance(node, Literal):
            return node.value

        if isinstance(node, Identifier):
            return self._resolve_identifier(node.name)

        if isinstance(node, BinaryOp):
            return self._eval_binop(node)

        if isinstance(node, UnaryOp):
            operand = self._eval(node.operand)
            if node.operator == "-":
                return -operand
            if node.operator == "not":
                return not self._truthy(operand)
            raise EvaluatorError(f"Unknown unary operator: {node.operator}")

        if isinstance(node, SeriesIndex):
            return self._eval_series_index(node)

        if isinstance(node, MemberAccess):
            return self._eval_member(node)

        if isinstance(node, FunctionCall):
            return self._eval_call(node)

        raise EvaluatorError(f"Cannot evaluate node type: {type(node).__name__}")

    def _eval_binop(self, node: BinaryOp) -> Any:
        left = self._eval(node.left)
        right = self._eval(node.right)
        op = node.operator

        # Handle None / NaN gracefully
        if left is None or right is None:
            return None
        if isinstance(left, float) and math.isnan(left):
            return NaN
        if isinstance(right, float) and math.isnan(right):
            return NaN

        if op == "+":   return left + right
        if op == "-":   return left - right
        if op == "*":   return left * right
        if op == "/":   return left / right if right != 0 else NaN
        if op == "%":   return left % right if right != 0 else NaN
        if op == ">":   return left > right
        if op == "<":   return left < right
        if op == ">=":  return left >= right
        if op == "<=":  return left <= right
        if op == "==":  return left == right
        if op == "!=":  return left != right
        if op == "and": return self._truthy(left) and self._truthy(right)
        if op == "or":  return self._truthy(left) or self._truthy(right)

        raise EvaluatorError(f"Unknown binary operator: {op!r}")

    # ── Identifier resolution ──────────────────────────────────────────────────

    def _resolve_identifier(self, name: str) -> Any:
        # OHLCV built-ins → current bar value
        if name in self._OHLCV:
            return self._bars[name][self._bar_index]

        # Env variable
        if name in self._env:
            return self._env[name]

        # Persistent var
        if name in self._persistent:
            return self._persistent[name]

        # Series variable (return latest value)
        if name in self._series and self._series[name]:
            return self._series[name][-1]

        return None  # na

    # ── Series indexing ────────────────────────────────────────────────────────

    def _eval_series_index(self, node: SeriesIndex) -> Any:
        offset = self._eval(node.index)
        if isinstance(node.series, Identifier):
            name = node.series.name
            target_bar = self._bar_index - int(offset)
            if target_bar < 0:
                return NaN
            if name in self._OHLCV:
                return self._bars[name][target_bar]
            if name in self._series:
                series = self._series[name]
                if target_bar < len(series):
                    return series[target_bar]
            return NaN
        # For computed series (e.g. ta.ema(close, 20)[1]), evaluate the series
        val = self._eval(node.series)
        if isinstance(val, list):
            idx = self._bar_index - int(offset)
            if 0 <= idx < len(val):
                return val[idx]
        return NaN

    # ── Member access ──────────────────────────────────────────────────────────

    def _eval_member(self, node: MemberAccess) -> Any:
        obj = node.object
        member = node.member

        if obj == "strategy":
            if member == "long":
                return "long"
            if member == "short":
                return "short"

        return f"{obj}.{member}"

    # ── Function calls ─────────────────────────────────────────────────────────

    def _eval_call(self, node: FunctionCall) -> Any:
        func = node.func

        if isinstance(func, MemberAccess):
            ns = func.object
            fn = func.member

            if ns == "ta":
                return self._call_ta(fn, node)

            if ns == "strategy":
                # strategy.entry / exit / close handled via StrategyEntry nodes
                # but if they arrive as FunctionCall, handle here too
                return self._call_strategy_fn(fn, node)

            if ns == "math":
                return self._call_math(fn, node)

        # Bare function call
        if isinstance(func, Identifier):
            return self._call_builtin(func.name, node)

        return None

    # ── ta.* dispatch ──────────────────────────────────────────────────────────

    def _call_ta(self, fn: str, node: FunctionCall) -> Any:
        """Dispatch ta.* functions, returning the *current bar* value."""

        def _get_series(arg_node: Node) -> List[float]:
            """Evaluate an argument as a full series list."""
            if isinstance(arg_node, Identifier) and arg_node.name in self._OHLCV:
                return self._bars[arg_node.name]
            val = self._eval(arg_node)
            if isinstance(val, list):
                return val
            # Scalar — expand to series of same length
            return [val] * self._n_bars

        def _get_int(arg_node: Node) -> int:
            return int(self._eval(arg_node))

        def _get_float(arg_node: Node) -> float:
            return float(self._eval(arg_node))

        args = node.args

        if fn == "sma":
            series = _get_series(args[0])
            length = _get_int(args[1] if len(args) > 1 else node.kwargs.get("length", Literal(value=20)))
            result = TaLib.sma(series, length)
            return result[self._bar_index]

        if fn == "ema":
            series = _get_series(args[0])
            length = _get_int(args[1] if len(args) > 1 else node.kwargs.get("length", Literal(value=20)))
            result = TaLib.ema(series, length)
            return result[self._bar_index]

        if fn == "rsi":
            src_node = args[0] if args else node.kwargs.get("source", Identifier(name="close"))
            len_node = args[1] if len(args) > 1 else node.kwargs.get("length", Literal(value=14))
            series = _get_series(src_node)
            length = _get_int(len_node)
            result = TaLib.rsi(series, length)
            return result[self._bar_index]

        if fn == "macd":
            series = _get_series(args[0])
            fast = _get_int(args[1]) if len(args) > 1 else 12
            slow = _get_int(args[2]) if len(args) > 2 else 26
            sig = _get_int(args[3]) if len(args) > 3 else 9
            macd_l, sig_l, hist = TaLib.macd(series, fast, slow, sig)
            return macd_l[self._bar_index]

        if fn == "bbands":
            series = _get_series(args[0])
            length = _get_int(args[1]) if len(args) > 1 else 20
            mult = _get_float(args[2]) if len(args) > 2 else 2.0
            upper, middle, lower = TaLib.bbands(series, length, mult)
            return upper[self._bar_index], middle[self._bar_index], lower[self._bar_index]

        if fn == "atr":
            length = _get_int(args[0]) if args else 14
            result = TaLib.atr(
                self._bars["high"], self._bars["low"], self._bars["close"], length
            )
            return result[self._bar_index]

        if fn == "crossover":
            s_a = _get_series(args[0])
            s_b = _get_series(args[1])
            result = TaLib.crossover(s_a, s_b)
            return result[self._bar_index]

        if fn == "crossunder":
            s_a = _get_series(args[0])
            s_b = _get_series(args[1])
            result = TaLib.crossunder(s_a, s_b)
            return result[self._bar_index]

        if fn == "highest":
            series = _get_series(args[0])
            length = _get_int(args[1]) if len(args) > 1 else 14
            result = TaLib.highest(series, length)
            return result[self._bar_index]

        if fn == "lowest":
            series = _get_series(args[0])
            length = _get_int(args[1]) if len(args) > 1 else 14
            result = TaLib.lowest(series, length)
            return result[self._bar_index]

        if fn == "stdev":
            series = _get_series(args[0])
            length = _get_int(args[1]) if len(args) > 1 else 20
            result = TaLib.stdev(series, length)
            return result[self._bar_index]

        # Unknown ta.* — return NaN
        return NaN

    def _call_math(self, fn: str, node: FunctionCall) -> Any:
        arg = self._eval(node.args[0]) if node.args else 0
        fns = {
            "abs": abs, "sqrt": math.sqrt, "log": math.log,
            "floor": math.floor, "ceil": math.ceil, "round": round,
            "max": max, "min": min,
        }
        if fn in fns:
            if fn in ("max", "min"):
                vals = [self._eval(a) for a in node.args]
                return fns[fn](vals)
            return fns[fn](arg)
        return NaN

    def _call_strategy_fn(self, fn: str, node: FunctionCall) -> Any:
        # Handled via StrategyEntry/Exit/Close nodes in _exec
        return None

    def _call_builtin(self, name: str, node: FunctionCall) -> Any:
        """Bare function calls (e.g. input.int, nz, etc.)."""
        if name in ("nz", "na"):
            # nz(x, y) → x if not na else y
            if len(node.args) >= 1:
                val = self._eval(node.args[0])
                if val is None or (isinstance(val, float) and math.isnan(val)):
                    return self._eval(node.args[1]) if len(node.args) > 1 else 0
                return val
        return None

    # ── Strategy signal emission ───────────────────────────────────────────────

    def _emit_entry(self, node: StrategyEntry) -> None:
        action = SignalAction.BUY if node.direction == "long" else SignalAction.SELL
        price = self._bars["close"][self._bar_index]

        stop_loss = None
        if node.stop:
            sl = self._eval(node.stop)
            stop_loss = float(sl) if sl is not None else None

        take_profit = None
        if node.limit:
            tp = self._eval(node.limit)
            take_profit = float(tp) if tp is not None else None

        sig = TradingSignal(
            source=SignalSource.PINE_SCRIPT,
            symbol=self._symbol,
            exchange=self._exchange,
            action=action,
            strategy_name=self._strategy_name,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata={"entry_id": node.id, "bar_index": self._bar_index},
        )
        self._signals.append(sig)

    def _emit_exit(self, node: StrategyExit) -> None:
        price = self._bars["close"][self._bar_index]
        sig = TradingSignal(
            source=SignalSource.PINE_SCRIPT,
            symbol=self._symbol,
            exchange=self._exchange,
            action=SignalAction.EXIT_LONG,
            strategy_name=self._strategy_name,
            price=price,
            metadata={
                "exit_id": node.id,
                "from_entry": node.from_entry,
                "bar_index": self._bar_index,
            },
        )
        self._signals.append(sig)

    def _emit_close(self, node: StrategyClose) -> None:
        price = self._bars["close"][self._bar_index]
        sig = TradingSignal(
            source=SignalSource.PINE_SCRIPT,
            symbol=self._symbol,
            exchange=self._exchange,
            action=SignalAction.EXIT,
            strategy_name=self._strategy_name,
            price=price,
            metadata={"close_id": node.id, "bar_index": self._bar_index},
        )
        self._signals.append(sig)

    # ── Utilities ──────────────────────────────────────────────────────────────

    @staticmethod
    def _truthy(val: Any) -> bool:
        if val is None:
            return False
        if isinstance(val, float) and math.isnan(val):
            return False
        return bool(val)
