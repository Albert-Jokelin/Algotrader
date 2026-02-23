"""TDD tests for Pine Script AST parser.

Covers parsing of common Pine Script v5 statement patterns.
Written BEFORE the implementation (Red phase).
"""

import pytest
from algotrader.pine.parser import Parser, ParseError
from algotrader.pine.ast_nodes import (
    Program,
    AssignStatement,
    VarDeclaration,
    IfStatement,
    FunctionCall,
    MemberAccess,
    BinaryOp,
    UnaryOp,
    Literal,
    Identifier,
    SeriesIndex,
    StrategyEntry,
    StrategyExit,
    StrategyClose,
    StrategyDeclaration,
    IndicatorDeclaration,
)
from algotrader.pine.lexer import Lexer


def parse(src: str) -> Program:
    tokens = Lexer(src).tokenize()
    return Parser(tokens).parse()


# ── Program node ──────────────────────────────────────────────────────────────

class TestProgram:
    def test_empty_program(self):
        prog = parse("")
        assert isinstance(prog, Program)
        assert prog.body == []

    def test_program_has_body(self):
        prog = parse("a = 1")
        assert len(prog.body) > 0


# ── Literals ──────────────────────────────────────────────────────────────────

class TestLiterals:
    def test_integer_literal(self):
        prog = parse("a = 42")
        assign = prog.body[0]
        assert isinstance(assign.value, Literal)
        assert assign.value.value == 42

    def test_float_literal(self):
        prog = parse("a = 3.14")
        assign = prog.body[0]
        assert isinstance(assign.value, Literal)
        assert assign.value.value == pytest.approx(3.14)

    def test_string_literal(self):
        prog = parse('a = "hello"')
        assign = prog.body[0]
        assert isinstance(assign.value, Literal)
        assert assign.value.value == "hello"

    def test_true_literal(self):
        prog = parse("a = true")
        assign = prog.body[0]
        assert isinstance(assign.value, Literal)
        assert assign.value.value is True

    def test_false_literal(self):
        prog = parse("a = false")
        assign = prog.body[0]
        assert isinstance(assign.value, Literal)
        assert assign.value.value is False

    def test_na_literal(self):
        prog = parse("a = na")
        assign = prog.body[0]
        assert isinstance(assign.value, Literal)
        assert assign.value.value is None


# ── Assignments ───────────────────────────────────────────────────────────────

class TestAssignments:
    def test_simple_assign(self):
        prog = parse("x = 10")
        stmt = prog.body[0]
        assert isinstance(stmt, AssignStatement)
        assert stmt.name == "x"

    def test_reassign_with_walrus(self):
        prog = parse("x := 10")
        stmt = prog.body[0]
        assert isinstance(stmt, AssignStatement)
        assert stmt.name == "x"
        assert stmt.is_reassign is True

    def test_var_declaration(self):
        prog = parse("var x = 0")
        stmt = prog.body[0]
        assert isinstance(stmt, VarDeclaration)
        assert stmt.name == "x"
        assert stmt.persistent is True

    def test_varip_declaration(self):
        prog = parse("varip x = 0")
        stmt = prog.body[0]
        assert isinstance(stmt, VarDeclaration)
        assert stmt.persistent is True


# ── Binary operations ─────────────────────────────────────────────────────────

class TestBinaryOps:
    def _binop(self, src) -> BinaryOp:
        prog = parse(f"r = {src}")
        return prog.body[0].value

    def test_addition(self):
        op = self._binop("a + b")
        assert isinstance(op, BinaryOp)
        assert op.operator == "+"

    def test_subtraction(self):
        op = self._binop("a - b")
        assert op.operator == "-"

    def test_multiplication(self):
        op = self._binop("a * b")
        assert op.operator == "*"

    def test_division(self):
        op = self._binop("a / b")
        assert op.operator == "/"

    def test_greater_than(self):
        op = self._binop("a > b")
        assert op.operator == ">"

    def test_less_than(self):
        op = self._binop("a < b")
        assert op.operator == "<"

    def test_equals(self):
        op = self._binop("a == b")
        assert op.operator == "=="

    def test_not_equals(self):
        op = self._binop("a != b")
        assert op.operator == "!="

    def test_and(self):
        op = self._binop("a and b")
        assert op.operator == "and"

    def test_or(self):
        op = self._binop("a or b")
        assert op.operator == "or"


# ── Series index ──────────────────────────────────────────────────────────────

class TestSeriesIndex:
    def test_close_prev_bar(self):
        prog = parse("r = close[1]")
        idx = prog.body[0].value
        assert isinstance(idx, SeriesIndex)
        assert isinstance(idx.series, Identifier)
        assert idx.series.name == "close"
        assert isinstance(idx.index, Literal)
        assert idx.index.value == 1

    def test_high_two_bars_ago(self):
        prog = parse("r = high[2]")
        idx = prog.body[0].value
        assert idx.index.value == 2


# ── Member access ─────────────────────────────────────────────────────────────

class TestMemberAccess:
    def test_ta_sma(self):
        prog = parse("r = ta.sma(close, 20)")
        call = prog.body[0].value
        assert isinstance(call, FunctionCall)
        assert isinstance(call.func, MemberAccess)
        assert call.func.object == "ta"
        assert call.func.member == "sma"

    def test_strategy_long(self):
        prog = parse("r = strategy.long")
        ma = prog.body[0].value
        assert isinstance(ma, MemberAccess)
        assert ma.object == "strategy"
        assert ma.member == "long"


# ── Function calls ────────────────────────────────────────────────────────────

class TestFunctionCalls:
    def test_simple_call_no_args(self):
        prog = parse("r = close()")
        call = prog.body[0].value
        assert isinstance(call, FunctionCall)
        assert len(call.args) == 0

    def test_call_with_positional_args(self):
        prog = parse("r = ta.sma(close, 20)")
        call = prog.body[0].value
        assert isinstance(call, FunctionCall)
        assert len(call.args) == 2

    def test_call_with_keyword_args(self):
        prog = parse('r = ta.rsi(source=close, length=14)')
        call = prog.body[0].value
        assert isinstance(call, FunctionCall)
        assert "source" in call.kwargs
        assert "length" in call.kwargs


# ── If statements ─────────────────────────────────────────────────────────────

class TestIfStatements:
    def test_if_no_else(self):
        src = "if close > open\n    x = 1"
        prog = parse(src)
        stmt = prog.body[0]
        assert isinstance(stmt, IfStatement)
        assert stmt.else_body is None

    def test_if_else(self):
        src = "if close > open\n    x = 1\nelse\n    x = 0"
        prog = parse(src)
        stmt = prog.body[0]
        assert isinstance(stmt, IfStatement)
        assert stmt.else_body is not None


# ── Strategy declarations ─────────────────────────────────────────────────────

class TestStrategyDeclaration:
    def test_strategy_decl(self):
        prog = parse('strategy("My Strat", overlay=true)')
        stmt = prog.body[0]
        assert isinstance(stmt, StrategyDeclaration)
        assert stmt.title == "My Strat"

    def test_indicator_decl(self):
        prog = parse('indicator("My Indicator", overlay=false)')
        stmt = prog.body[0]
        assert isinstance(stmt, IndicatorDeclaration)
        assert stmt.title == "My Indicator"


# ── Strategy entry/exit calls ─────────────────────────────────────────────────

class TestStrategyActions:
    def test_strategy_entry_long(self):
        prog = parse('strategy.entry("Long", strategy.long)')
        stmt = prog.body[0]
        assert isinstance(stmt, StrategyEntry)
        assert stmt.id == "Long"
        assert stmt.direction == "long"

    def test_strategy_entry_short(self):
        prog = parse('strategy.entry("Short", strategy.short)')
        stmt = prog.body[0]
        assert isinstance(stmt, StrategyEntry)
        assert stmt.direction == "short"

    def test_strategy_exit(self):
        prog = parse('strategy.exit("Exit Long", from_entry="Long", stop=stop_price, limit=tp_price)')
        stmt = prog.body[0]
        assert isinstance(stmt, StrategyExit)
        assert stmt.id == "Exit Long"
        assert stmt.from_entry == "Long"

    def test_strategy_close(self):
        prog = parse('strategy.close("Long")')
        stmt = prog.body[0]
        assert isinstance(stmt, StrategyClose)
        assert stmt.id == "Long"


# ── Error handling ────────────────────────────────────────────────────────────

class TestParserErrors:
    def test_unclosed_paren(self):
        with pytest.raises(ParseError):
            parse("r = ta.sma(close, 20")

    def test_unexpected_token(self):
        with pytest.raises(ParseError):
            parse("= 5")
