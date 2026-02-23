"""Pine Script v5 lexer (tokeniser).

Produces a flat list of Token objects from a Pine Script source string.
Supports a subset of Pine Script v5 sufficient for common trading strategies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import List


# ── Token types ───────────────────────────────────────────────────────────────

class TokenType(Enum):
    NUMBER = auto()
    STRING = auto()
    IDENTIFIER = auto()
    KEYWORD = auto()
    OPERATOR = auto()
    LPAREN = auto()
    RPAREN = auto()
    LBRACKET = auto()
    RBRACKET = auto()
    LBRACE = auto()
    RBRACE = auto()
    COMMA = auto()
    DOT = auto()
    COLON = auto()
    NEWLINE = auto()
    EOF = auto()


# Reserved Pine Script keywords
_KEYWORDS = frozenset({
    "if", "else", "for", "while", "to", "by", "do",
    "var", "varip",
    "true", "false", "na",
    "and", "or", "not",
    "import", "export", "method", "type",
    "switch", "return", "break", "continue",
    "series", "simple", "input",
    # Note: "strategy", "indicator", "ta", "math", "array" etc. are Pine Script
    # *namespaces* used as identifiers (e.g. strategy.entry, ta.sma), not keywords.
})


@dataclass
class Token:
    type: TokenType
    value: str
    line: int
    col: int

    def __repr__(self) -> str:  # pragma: no cover
        return f"Token({self.type.name}, {self.value!r}, L{self.line}:C{self.col})"


# ── Lexer ─────────────────────────────────────────────────────────────────────

class LexerError(Exception):
    pass


class Lexer:
    """Tokenise a Pine Script source string."""

    # Order matters: longer patterns first.
    _RULES: list[tuple[str, TokenType | None]] = [
        (r"[ \t]+",                         None),           # whitespace — skip
        (r"//[^\n]*",                        None),           # line comment — skip
        (r"\n",                              TokenType.NEWLINE),
        (r"\d+\.\d+",                        TokenType.NUMBER),
        (r"\d+",                             TokenType.NUMBER),
        (r'"(?:[^"\\]|\\.)*"',              TokenType.STRING),
        (r"'(?:[^'\\]|\\.)*'",              TokenType.STRING),
        (r":=",                              TokenType.OPERATOR),
        (r"==",                              TokenType.OPERATOR),
        (r"!=",                              TokenType.OPERATOR),
        (r">=",                              TokenType.OPERATOR),
        (r"<=",                              TokenType.OPERATOR),
        (r"=>",                              TokenType.OPERATOR),
        (r"[+\-*/%]",                        TokenType.OPERATOR),
        (r"[><=!]",                          TokenType.OPERATOR),
        (r"\(",                              TokenType.LPAREN),
        (r"\)",                              TokenType.RPAREN),
        (r"\[",                              TokenType.LBRACKET),
        (r"\]",                              TokenType.RBRACKET),
        (r"\{",                              TokenType.LBRACE),
        (r"\}",                              TokenType.RBRACE),
        (r",",                               TokenType.COMMA),
        (r"\.",                              TokenType.DOT),
        (r":",                               TokenType.COLON),
        (r"[A-Za-z_][A-Za-z0-9_]*",         TokenType.IDENTIFIER),  # identifiers & keywords
    ]

    def __init__(self, source: str) -> None:
        self._source = source
        self._compiled = [
            (re.compile(pattern), tok_type)
            for pattern, tok_type in self._RULES
        ]

    def tokenize(self) -> List[Token]:
        tokens: List[Token] = []
        pos = 0
        line = 1
        line_start = 0
        src = self._source

        while pos < len(src):
            matched = False
            for regex, tok_type in self._compiled:
                m = regex.match(src, pos)
                if m:
                    raw = m.group(0)
                    col = pos - line_start + 1

                    if tok_type == TokenType.NEWLINE:
                        tokens.append(Token(TokenType.NEWLINE, "\\n", line, col))
                        line += 1
                        line_start = m.end()
                    elif tok_type == TokenType.STRING:
                        # Strip surrounding quotes
                        inner = raw[1:-1]
                        tokens.append(Token(TokenType.STRING, inner, line, col))
                    elif tok_type == TokenType.IDENTIFIER:
                        # Promote keywords
                        tt = TokenType.KEYWORD if raw in _KEYWORDS else TokenType.IDENTIFIER
                        tokens.append(Token(tt, raw, line, col))
                    elif tok_type is not None:
                        tokens.append(Token(tok_type, raw, line, col))
                    # None tok_type = silently skipped (whitespace / comments)

                    pos = m.end()
                    matched = True
                    break

            if not matched:
                raise LexerError(
                    f"Unexpected character {src[pos]!r} at line {line}, col {pos - line_start + 1}"
                )

        tokens.append(Token(TokenType.EOF, "", line, pos - line_start + 1))
        return tokens
