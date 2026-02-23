"""Pine Script v5 recursive-descent parser.

Converts a flat token list (from the Lexer) into an AST (Program node).
Supports the subset of Pine Script needed for common trading strategies:
  - Variable declarations (var/varip) and assignments
  - Arithmetic, comparison, and logical expressions
  - Series indexing: close[1]
  - Member access: ta.sma, strategy.long
  - Function calls (positional + keyword args)
  - if/else blocks
  - strategy() / indicator() declarations
  - strategy.entry() / strategy.exit() / strategy.close()
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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
from algotrader.pine.lexer import Token, TokenType


class ParseError(Exception):
    pass


class Parser:
    """Recursive-descent parser for Pine Script."""

    def __init__(self, tokens: List[Token]) -> None:
        self._tokens = [t for t in tokens if t.type != TokenType.NEWLINE or self._keep_newline(tokens, t)]
        # Actually we need newlines for block detection; keep all but collapse multiples
        self._tokens = self._collapse_newlines(tokens)
        self._pos = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def parse(self) -> Program:
        stmts: List[Node] = []
        while not self._at_eof():
            self._skip_newlines()
            if self._at_eof():
                break
            stmt = self._parse_statement()
            if stmt is not None:
                stmts.append(stmt)
        return Program(body=stmts)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _keep_newline(tokens, t):  # pragma: no cover
        return True

    @staticmethod
    def _collapse_newlines(tokens: List[Token]) -> List[Token]:
        """Collapse runs of consecutive NEWLINEs into a single one."""
        result: List[Token] = []
        prev_was_nl = False
        for tok in tokens:
            if tok.type == TokenType.NEWLINE:
                if not prev_was_nl:
                    result.append(tok)
                prev_was_nl = True
            else:
                result.append(tok)
                prev_was_nl = False
        return result

    def _at_eof(self) -> bool:
        return self._peek().type == TokenType.EOF

    def _peek(self, offset: int = 0) -> Token:
        idx = self._pos + offset
        if idx >= len(self._tokens):
            return self._tokens[-1]  # EOF token
        return self._tokens[idx]

    def _advance(self) -> Token:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def _skip_newlines(self) -> None:
        while self._peek().type == TokenType.NEWLINE:
            self._advance()

    def _expect(self, tok_type: TokenType, value: Optional[str] = None) -> Token:
        tok = self._peek()
        if tok.type != tok_type:
            raise ParseError(
                f"Expected {tok_type.name} but got {tok.type.name}({tok.value!r}) "
                f"at line {tok.line}"
            )
        if value is not None and tok.value != value:
            raise ParseError(
                f"Expected {value!r} but got {tok.value!r} at line {tok.line}"
            )
        return self._advance()

    def _match(self, tok_type: TokenType, value: Optional[str] = None) -> bool:
        tok = self._peek()
        if tok.type != tok_type:
            return False
        if value is not None and tok.value != value:
            return False
        return True

    def _consume_if(self, tok_type: TokenType, value: Optional[str] = None) -> Optional[Token]:
        if self._match(tok_type, value):
            return self._advance()
        return None

    # ── Statement parsing ─────────────────────────────────────────────────────

    def _parse_statement(self) -> Optional[Node]:
        tok = self._peek()

        # var / varip declaration
        if tok.type == TokenType.KEYWORD and tok.value in ("var", "varip"):
            return self._parse_var_decl()

        # if statement
        if tok.type == TokenType.KEYWORD and tok.value == "if":
            return self._parse_if()

        # strategy(...) declaration
        if tok.type == TokenType.IDENTIFIER and tok.value == "strategy":
            if self._peek(1).type == TokenType.LPAREN:
                return self._parse_strategy_decl()
            if self._peek(1).type == TokenType.DOT:
                return self._parse_strategy_action()

        # indicator(...) declaration
        if tok.type == TokenType.IDENTIFIER and tok.value == "indicator":
            if self._peek(1).type == TokenType.LPAREN:
                return self._parse_indicator_decl()

        # assignment or expression statement
        return self._parse_assign_or_expr()

    def _parse_var_decl(self) -> VarDeclaration:
        tok = self._advance()  # consume var / varip
        name_tok = self._expect(TokenType.IDENTIFIER)
        self._expect(TokenType.OPERATOR, "=")
        value = self._parse_expression()
        self._consume_if(TokenType.NEWLINE)
        return VarDeclaration(
            name=name_tok.value,
            value=value,
            persistent=True,
            line=tok.line,
        )

    def _parse_assign_or_expr(self) -> Node:
        """Parse `name = expr`, `name := expr`, or a bare expression."""
        # Peek-ahead to see if this is an assignment
        if (
            self._peek().type == TokenType.IDENTIFIER
            and self._peek(1).type == TokenType.OPERATOR
            and self._peek(1).value in ("=", ":=")
        ):
            name_tok = self._advance()
            op_tok = self._advance()
            value = self._parse_expression()
            self._consume_if(TokenType.NEWLINE)
            return AssignStatement(
                name=name_tok.value,
                value=value,
                is_reassign=(op_tok.value == ":="),
                line=name_tok.line,
            )

        expr = self._parse_expression()
        self._consume_if(TokenType.NEWLINE)
        return expr

    def _parse_if(self) -> IfStatement:
        tok = self._advance()  # consume "if"
        condition = self._parse_expression()
        self._consume_if(TokenType.NEWLINE)
        body = self._parse_indented_block()

        else_body = None
        self._skip_newlines()
        if self._match(TokenType.KEYWORD, "else"):
            self._advance()
            self._consume_if(TokenType.NEWLINE)
            else_body = self._parse_indented_block()

        return IfStatement(condition=condition, body=body, else_body=else_body, line=tok.line)

    def _parse_indented_block(self) -> List[Node]:
        """Parse statements that are indented (4 spaces or 1 tab) after an if/else."""
        stmts: List[Node] = []
        while not self._at_eof():
            self._skip_newlines()
            tok = self._peek()
            # Stop if we hit EOF, unindented keyword, or else
            if tok.type == TokenType.EOF:
                break
            if tok.type == TokenType.KEYWORD and tok.value in ("else",):
                break
            stmt = self._parse_statement()
            if stmt is not None:
                stmts.append(stmt)
            # Only parse one statement per indented block for simplicity
            break
        return stmts

    # ── Strategy declarations ─────────────────────────────────────────────────

    def _parse_strategy_decl(self) -> StrategyDeclaration:
        tok = self._advance()  # "strategy"
        self._expect(TokenType.LPAREN)
        args, kwargs = self._parse_arg_list()
        self._expect(TokenType.RPAREN)
        self._consume_if(TokenType.NEWLINE)

        title = args[0].value if args and isinstance(args[0], Literal) else ""
        overlay = self._resolve_bool(kwargs.get("overlay"), default=False)
        return StrategyDeclaration(title=title, overlay=overlay, line=tok.line)

    def _parse_indicator_decl(self) -> IndicatorDeclaration:
        tok = self._advance()  # "indicator"
        self._expect(TokenType.LPAREN)
        args, kwargs = self._parse_arg_list()
        self._expect(TokenType.RPAREN)
        self._consume_if(TokenType.NEWLINE)

        title = args[0].value if args and isinstance(args[0], Literal) else ""
        overlay = self._resolve_bool(kwargs.get("overlay"), default=False)
        return IndicatorDeclaration(title=title, overlay=overlay, line=tok.line)

    @staticmethod
    def _resolve_bool(node: Optional[Node], default: bool = False) -> bool:
        if node is None:
            return default
        if isinstance(node, Literal):
            return bool(node.value)
        return default

    # ── Strategy action parsing ───────────────────────────────────────────────

    def _parse_strategy_action(self) -> Node:
        """Parse strategy.entry / strategy.exit / strategy.close."""
        self._advance()  # "strategy"
        self._expect(TokenType.DOT)
        action_tok = self._expect(TokenType.IDENTIFIER)
        action = action_tok.value

        self._expect(TokenType.LPAREN)
        args, kwargs = self._parse_arg_list()
        self._expect(TokenType.RPAREN)
        self._consume_if(TokenType.NEWLINE)

        if action == "entry":
            return self._build_strategy_entry(args, kwargs, action_tok.line)
        if action == "exit":
            return self._build_strategy_exit(args, kwargs, action_tok.line)
        if action == "close":
            entry_id = args[0].value if args and isinstance(args[0], Literal) else ""
            return StrategyClose(id=entry_id, kwargs=kwargs, line=action_tok.line)

        # Fallback — treat as generic function call
        func = MemberAccess(object="strategy", member=action, line=action_tok.line)
        return FunctionCall(func=func, args=args, kwargs=kwargs, line=action_tok.line)

    def _build_strategy_entry(
        self, args: List[Node], kwargs: Dict[str, Node], line: int
    ) -> StrategyEntry:
        entry_id = args[0].value if args and isinstance(args[0], Literal) else ""
        direction = "long"
        if len(args) >= 2:
            d = args[1]
            if isinstance(d, MemberAccess):
                direction = d.member  # "long" or "short"
        return StrategyEntry(
            id=entry_id,
            direction=direction,
            qty=kwargs.get("qty"),
            limit=kwargs.get("limit"),
            stop=kwargs.get("stop"),
            kwargs={k: v for k, v in kwargs.items() if k not in ("qty", "limit", "stop")},
            line=line,
        )

    def _build_strategy_exit(
        self, args: List[Node], kwargs: Dict[str, Node], line: int
    ) -> StrategyExit:
        exit_id = args[0].value if args and isinstance(args[0], Literal) else ""
        from_entry = ""
        if "from_entry" in kwargs and isinstance(kwargs["from_entry"], Literal):
            from_entry = str(kwargs["from_entry"].value)
        return StrategyExit(
            id=exit_id,
            from_entry=from_entry,
            qty=kwargs.get("qty"),
            limit=kwargs.get("limit"),
            stop=kwargs.get("stop"),
            kwargs={k: v for k, v in kwargs.items() if k not in ("qty", "limit", "stop", "from_entry")},
            line=line,
        )

    # ── Expression parsing (Pratt / precedence climbing) ─────────────────────

    _BINARY_PREC: dict[str, int] = {
        "or": 1,
        "and": 2,
        "==": 3, "!=": 3,
        "<": 4, ">": 4, "<=": 4, ">=": 4,
        "+": 5, "-": 5,
        "*": 6, "/": 6, "%": 6,
    }

    def _parse_expression(self, min_prec: int = 0) -> Node:
        left = self._parse_unary()

        while True:
            tok = self._peek()
            op = tok.value
            prec = self._BINARY_PREC.get(op)
            if prec is None or prec <= min_prec:
                break
            self._advance()
            right = self._parse_expression(min_prec=prec)
            left = BinaryOp(left=left, operator=op, right=right, line=tok.line)

        return left

    def _parse_unary(self) -> Node:
        tok = self._peek()
        if tok.type == TokenType.OPERATOR and tok.value == "-":
            self._advance()
            operand = self._parse_unary()
            return UnaryOp(operator="-", operand=operand, line=tok.line)
        if tok.type == TokenType.KEYWORD and tok.value == "not":
            self._advance()
            operand = self._parse_unary()
            return UnaryOp(operator="not", operand=operand, line=tok.line)
        return self._parse_postfix()

    def _parse_postfix(self) -> Node:
        node = self._parse_primary()

        while True:
            if self._match(TokenType.DOT):
                self._advance()
                member_tok = self._expect(TokenType.IDENTIFIER)
                if self._match(TokenType.LPAREN):
                    # Method call: node.member(...)
                    self._advance()
                    args, kwargs = self._parse_arg_list()
                    self._expect(TokenType.RPAREN)
                    if isinstance(node, Identifier):
                        func = MemberAccess(object=node.name, member=member_tok.value, line=member_tok.line)
                    else:
                        func = MemberAccess(object=str(node), member=member_tok.value, line=member_tok.line)
                    node = FunctionCall(func=func, args=args, kwargs=kwargs, line=member_tok.line)
                else:
                    # Property access: node.member
                    if isinstance(node, Identifier):
                        node = MemberAccess(object=node.name, member=member_tok.value, line=member_tok.line)
            elif self._match(TokenType.LBRACKET):
                self._advance()
                index = self._parse_expression()
                self._expect(TokenType.RBRACKET)
                node = SeriesIndex(series=node, index=index, line=node.line)
            elif self._match(TokenType.LPAREN):
                # Function call: node(...)
                self._advance()
                args, kwargs = self._parse_arg_list()
                self._expect(TokenType.RPAREN)
                node = FunctionCall(func=node, args=args, kwargs=kwargs, line=node.line)
            else:
                break

        return node

    def _parse_primary(self) -> Node:
        tok = self._peek()

        # Grouped expression
        if tok.type == TokenType.LPAREN:
            self._advance()
            expr = self._parse_expression()
            if not self._match(TokenType.RPAREN):
                raise ParseError(f"Expected ')' at line {self._peek().line}")
            self._advance()
            return expr

        # Number literal
        if tok.type == TokenType.NUMBER:
            self._advance()
            val = float(tok.value) if "." in tok.value else int(tok.value)
            return Literal(value=val, line=tok.line)

        # String literal
        if tok.type == TokenType.STRING:
            self._advance()
            return Literal(value=tok.value, line=tok.line)

        # Keywords that represent values
        if tok.type == TokenType.KEYWORD:
            self._advance()
            if tok.value == "true":
                return Literal(value=True, line=tok.line)
            if tok.value == "false":
                return Literal(value=False, line=tok.line)
            if tok.value == "na":
                return Literal(value=None, line=tok.line)
            # Other keywords used as identifiers (e.g. "and", "or" handled in expression)
            return Identifier(name=tok.value, line=tok.line)

        # Identifier
        if tok.type == TokenType.IDENTIFIER:
            self._advance()
            return Identifier(name=tok.value, line=tok.line)

        raise ParseError(
            f"Unexpected token {tok.type.name}({tok.value!r}) at line {tok.line}"
        )

    # ── Argument list parsing ─────────────────────────────────────────────────

    def _parse_arg_list(self):
        """Parse a comma-separated list of positional and keyword args.

        Returns (positional_args, keyword_args).
        Keyword args: identifier followed by "=" expression.
        """
        args: List[Node] = []
        kwargs: Dict[str, Node] = {}

        if self._match(TokenType.RPAREN):
            return args, kwargs

        while True:
            # Keyword arg?
            if (
                self._peek().type == TokenType.IDENTIFIER
                and self._peek(1).type == TokenType.OPERATOR
                and self._peek(1).value == "="
            ):
                key_tok = self._advance()
                self._advance()  # "="
                val = self._parse_expression()
                kwargs[key_tok.value] = val
            else:
                val = self._parse_expression()
                args.append(val)

            if not self._consume_if(TokenType.COMMA):
                break

        return args, kwargs
