"""Pine Script AST node definitions.

Each node represents a syntactic construct in Pine Script v5.
Nodes are plain dataclasses for easy traversal and testing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── Base ──────────────────────────────────────────────────────────────────────

@dataclass
class Node:
    """Abstract base for all AST nodes."""
    line: int = 0


# ── Expressions ───────────────────────────────────────────────────────────────

@dataclass
class Literal(Node):
    """A constant value: number, string, bool, or na (None)."""
    value: Any = None


@dataclass
class Identifier(Node):
    """A bare name, e.g. `close`, `myVar`."""
    name: str = ""


@dataclass
class MemberAccess(Node):
    """Dotted access: `ta.sma`, `strategy.long`."""
    object: str = ""
    member: str = ""


@dataclass
class SeriesIndex(Node):
    """Historical bar access: `close[1]`."""
    series: Node = field(default_factory=Identifier)
    index: Node = field(default_factory=lambda: Literal(value=0))


@dataclass
class BinaryOp(Node):
    """Binary operation: `a + b`, `close > open`, `x and y`."""
    left: Node = field(default_factory=Identifier)
    operator: str = ""
    right: Node = field(default_factory=Identifier)


@dataclass
class UnaryOp(Node):
    """Unary operation: `-x`, `not cond`."""
    operator: str = ""
    operand: Node = field(default_factory=Identifier)


@dataclass
class FunctionCall(Node):
    """A function/method call with positional and keyword arguments."""
    func: Node = field(default_factory=Identifier)          # Identifier or MemberAccess
    args: List[Node] = field(default_factory=list)
    kwargs: Dict[str, Node] = field(default_factory=dict)


# ── Statements ────────────────────────────────────────────────────────────────

@dataclass
class AssignStatement(Node):
    """Variable assignment: `x = expr` or `x := expr`."""
    name: str = ""
    value: Node = field(default_factory=Literal)
    is_reassign: bool = False          # True for `:=`


@dataclass
class VarDeclaration(Node):
    """`var x = expr` — persistent across bars."""
    name: str = ""
    value: Node = field(default_factory=Literal)
    persistent: bool = True            # True for both `var` and `varip`


@dataclass
class IfStatement(Node):
    """if/else statement."""
    condition: Node = field(default_factory=Literal)
    body: List[Node] = field(default_factory=list)
    else_body: Optional[List[Node]] = None


# ── Top-level declarations ────────────────────────────────────────────────────

@dataclass
class StrategyDeclaration(Node):
    """strategy("Title", ...) — marks the script as a strategy."""
    title: str = ""
    overlay: bool = False
    kwargs: Dict[str, Any] = field(default_factory=dict)


@dataclass
class IndicatorDeclaration(Node):
    """indicator("Title", ...) — marks the script as an indicator."""
    title: str = ""
    overlay: bool = False
    kwargs: Dict[str, Any] = field(default_factory=dict)


# ── Strategy action nodes ─────────────────────────────────────────────────────

@dataclass
class StrategyEntry(Node):
    """strategy.entry("id", strategy.long/short, ...)"""
    id: str = ""
    direction: str = "long"    # "long" | "short"
    qty: Optional[Node] = None
    limit: Optional[Node] = None
    stop: Optional[Node] = None
    kwargs: Dict[str, Node] = field(default_factory=dict)


@dataclass
class StrategyExit(Node):
    """strategy.exit("id", from_entry="entry_id", ...)"""
    id: str = ""
    from_entry: str = ""
    qty: Optional[Node] = None
    limit: Optional[Node] = None
    stop: Optional[Node] = None
    kwargs: Dict[str, Node] = field(default_factory=dict)


@dataclass
class StrategyClose(Node):
    """strategy.close("id")"""
    id: str = ""
    kwargs: Dict[str, Node] = field(default_factory=dict)


# ── Program root ──────────────────────────────────────────────────────────────

@dataclass
class Program:
    """Root node — contains a list of top-level statements."""
    body: List[Node] = field(default_factory=list)
