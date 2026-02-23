"""TDD tests for Pine Script lexer.

Tests cover tokenisation of common Pine Script v5 constructs.
Written BEFORE the implementation (Red phase).
"""

import pytest
from algotrader.pine.lexer import Lexer, Token, TokenType


class TestTokenTypes:
    """Verify the token type enumeration exists and has the right members."""

    def test_has_number(self):
        assert hasattr(TokenType, "NUMBER")

    def test_has_string(self):
        assert hasattr(TokenType, "STRING")

    def test_has_identifier(self):
        assert hasattr(TokenType, "IDENTIFIER")

    def test_has_keyword(self):
        assert hasattr(TokenType, "KEYWORD")

    def test_has_operator(self):
        assert hasattr(TokenType, "OPERATOR")

    def test_has_lparen(self):
        assert hasattr(TokenType, "LPAREN")

    def test_has_rparen(self):
        assert hasattr(TokenType, "RPAREN")

    def test_has_comma(self):
        assert hasattr(TokenType, "COMMA")

    def test_has_newline(self):
        assert hasattr(TokenType, "NEWLINE")

    def test_has_eof(self):
        assert hasattr(TokenType, "EOF")


class TestLexerBasics:
    def test_empty_source(self):
        tokens = Lexer("").tokenize()
        assert tokens[-1].type == TokenType.EOF

    def test_integer_literal(self):
        tokens = Lexer("42").tokenize()
        assert tokens[0].type == TokenType.NUMBER
        assert tokens[0].value == "42"

    def test_float_literal(self):
        tokens = Lexer("3.14").tokenize()
        assert tokens[0].type == TokenType.NUMBER
        assert tokens[0].value == "3.14"

    def test_negative_number(self):
        tokens = Lexer("-5").tokenize()
        # Minus is an operator; 5 is a number
        types = [t.type for t in tokens if t.type != TokenType.EOF]
        assert TokenType.OPERATOR in types
        assert TokenType.NUMBER in types

    def test_string_double_quotes(self):
        tokens = Lexer('"hello"').tokenize()
        assert tokens[0].type == TokenType.STRING
        assert tokens[0].value == "hello"

    def test_identifier(self):
        tokens = Lexer("close").tokenize()
        assert tokens[0].type == TokenType.IDENTIFIER
        assert tokens[0].value == "close"

    def test_keyword_if(self):
        tokens = Lexer("if").tokenize()
        assert tokens[0].type == TokenType.KEYWORD

    def test_keyword_for(self):
        tokens = Lexer("for").tokenize()
        assert tokens[0].type == TokenType.KEYWORD

    def test_keyword_var(self):
        tokens = Lexer("var").tokenize()
        assert tokens[0].type == TokenType.KEYWORD

    def test_keyword_true(self):
        tokens = Lexer("true").tokenize()
        assert tokens[0].type == TokenType.KEYWORD

    def test_keyword_false(self):
        tokens = Lexer("false").tokenize()
        assert tokens[0].type == TokenType.KEYWORD

    def test_keyword_na(self):
        tokens = Lexer("na").tokenize()
        assert tokens[0].type == TokenType.KEYWORD


class TestLexerOperators:
    def _ops(self, src):
        return [t for t in Lexer(src).tokenize() if t.type == TokenType.OPERATOR]

    def test_plus(self):
        assert self._ops("a + b")[0].value == "+"

    def test_minus(self):
        assert self._ops("a - b")[0].value == "-"

    def test_multiply(self):
        assert self._ops("a * b")[0].value == "*"

    def test_divide(self):
        assert self._ops("a / b")[0].value == "/"

    def test_equals(self):
        assert self._ops("a == b")[0].value == "=="

    def test_not_equals(self):
        assert self._ops("a != b")[0].value == "!="

    def test_greater_than(self):
        assert self._ops("a > b")[0].value == ">"

    def test_less_than(self):
        assert self._ops("a < b")[0].value == "<"

    def test_greater_equal(self):
        assert self._ops("a >= b")[0].value == ">="

    def test_less_equal(self):
        assert self._ops("a <= b")[0].value == "<="

    def test_assign(self):
        assert self._ops("a := b")[0].value == ":="

    def test_declare(self):
        assert self._ops("a = b")[0].value == "="


class TestLexerPunctuation:
    def test_lparen(self):
        tokens = Lexer("(").tokenize()
        assert tokens[0].type == TokenType.LPAREN

    def test_rparen(self):
        tokens = Lexer(")").tokenize()
        assert tokens[0].type == TokenType.RPAREN

    def test_comma(self):
        tokens = Lexer(",").tokenize()
        assert tokens[0].type == TokenType.COMMA

    def test_dot(self):
        tokens = Lexer(".").tokenize()
        assert tokens[0].type == TokenType.DOT

    def test_lbracket(self):
        tokens = Lexer("[").tokenize()
        assert tokens[0].type == TokenType.LBRACKET

    def test_rbracket(self):
        tokens = Lexer("]").tokenize()
        assert tokens[0].type == TokenType.RBRACKET


class TestLexerComments:
    def test_line_comment_ignored(self):
        tokens = Lexer("// this is a comment").tokenize()
        non_eof = [t for t in tokens if t.type != TokenType.EOF]
        assert len(non_eof) == 0

    def test_inline_comment_does_not_consume_code(self):
        tokens = Lexer("close // price series").tokenize()
        assert tokens[0].type == TokenType.IDENTIFIER
        assert tokens[0].value == "close"


class TestLexerExpressions:
    def test_function_call(self):
        tokens = Lexer("ta.sma(close, 20)").tokenize()
        types = [t.type for t in tokens if t.type != TokenType.EOF]
        # ta, ., sma, (, close, 20, )
        assert TokenType.IDENTIFIER in types
        assert TokenType.DOT in types
        assert TokenType.LPAREN in types
        assert TokenType.NUMBER in types
        assert TokenType.RPAREN in types

    def test_series_index(self):
        tokens = Lexer("close[1]").tokenize()
        types = [t.type for t in tokens if t.type != TokenType.EOF]
        assert TokenType.LBRACKET in types
        assert TokenType.NUMBER in types
        assert TokenType.RBRACKET in types

    def test_multiline(self):
        src = "a = 1\nb = 2"
        tokens = Lexer(src).tokenize()
        has_newline = any(t.type == TokenType.NEWLINE for t in tokens)
        assert has_newline

    def test_strategy_entry_tokens(self):
        src = 'strategy.entry("Long", strategy.long)'
        tokens = Lexer(src).tokenize()
        values = [t.value for t in tokens if t.type == TokenType.IDENTIFIER]
        assert "strategy" in values
        assert "entry" in values

    def test_line_numbers_tracked(self):
        src = "a = 1\nb = 2"
        tokens = Lexer(src).tokenize()
        lines = [t.line for t in tokens if t.type == TokenType.NUMBER]
        assert 1 in lines
        assert 2 in lines
