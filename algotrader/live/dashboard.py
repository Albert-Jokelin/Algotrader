"""Rich terminal dashboard for the live trading bot.

Renders a real-time status panel in the terminal using the ``rich`` library.
Updated on every bar tick.

Layout
------
┌─ AlgoTrader Live ─────────────────────────────────────────────────────────┐
│ Status: RUNNING  |  Symbol: NSE:RELIANCE  |  Bar #42  |  09:57:00 IST    │
├───────────────────────────────────────────────────────────────────────────┤
│ POSITIONS                                                                 │
│  Symbol      Qty   Avg Price   Mark Price   Unrealised P&L   Product     │
│  RELIANCE     50   2 450.00    2 463.50      +675.00 (+0.27%)  NRML      │
├───────────────────────────────────────────────────────────────────────────┤
│ EQUITY                                                                    │
│  Capital: ₹10,00,000   Equity: ₹10,00,675   Day P&L: +675.00 (+0.07%)   │
│  Max Drawdown: -0.12%  Charges Today: ₹48.30                             │
├───────────────────────────────────────────────────────────────────────────┤
│ RECENT FILLS (last 5)                                                     │
│  Time      Symbol     Side  Qty   Price    Charges                       │
│  09:15:01  RELIANCE   BUY    50   2450.00  ₹48.30                        │
└───────────────────────────────────────────────────────────────────────────┘

Usage::

    from algotrader.live.dashboard import LiveDashboard
    dash = LiveDashboard(symbol="RELIANCE", exchange="NSE", strategy="ma_cross")

    # Call after each bar:
    dash.update(
        bar_num=1,
        positions=broker.get_positions(),
        fills=broker.fills,
        equity=broker.available_capital,
        initial_capital=1_000_000,
        mark_price=2463.50,
    )
    dash.render()   # prints to terminal

Edge constraints
----------------
* Falls back to plain-text output if ``rich`` is not importable.
* Non-blocking: never sleeps; caller controls the update rate.
* Thread-safe: all state is held in the Dashboard instance; ``render()`` can
  be called from the main thread while a background poller updates fills.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from algotrader.signals.models import Fill, OrderSide, Position

_IST = timezone(timedelta(hours=5, minutes=30))

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich.columns import Columns
    from rich import box
    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False


def _ist_now() -> str:
    return datetime.now(_IST).strftime("%H:%M:%S IST")


def _pnl_str(pnl: float) -> str:
    sign = "+" if pnl >= 0 else ""
    return f"{sign}₹{pnl:,.2f}"


def _pct_str(pnl: float, base: float) -> str:
    if base == 0:
        return ""
    pct = pnl / base * 100
    sign = "+" if pct >= 0 else ""
    return f"({sign}{pct:.2f}%)"


class LiveDashboard:
    """Collects live-trading state and renders a rich terminal display.

    Call ``update()`` once per bar, then ``render()`` to print to stdout.
    """

    def __init__(
        self,
        symbol: str,
        exchange: str = "NSE",
        strategy: str = "live",
        max_fills_shown: int = 10,
    ) -> None:
        self.symbol = symbol
        self.exchange = exchange
        self.strategy = strategy
        self.max_fills_shown = max_fills_shown

        # State — updated by update()
        self._bar_num: int = 0
        self._positions: List[Position] = []
        self._fills: List[Fill] = []
        self._equity: float = 0.0
        self._initial_capital: float = 0.0
        self._mark_price: float = 0.0
        self._daily_charges: float = 0.0
        self._peak_equity: float = 0.0
        self._max_drawdown_pct: float = 0.0
        self._status: str = "RUNNING"

        self._lock = threading.Lock()

        if _RICH_AVAILABLE:
            self._console = Console()

    def update(
        self,
        bar_num: int,
        positions: List[Position],
        fills: List[Fill],
        equity: float,
        initial_capital: float,
        mark_price: float = 0.0,
        status: str = "RUNNING",
    ) -> None:
        """Refresh internal state.  Call once per bar."""
        with self._lock:
            self._bar_num = bar_num
            self._positions = list(positions)
            self._fills = list(fills)
            self._equity = equity
            self._initial_capital = initial_capital
            self._mark_price = mark_price
            self._status = status

            self._daily_charges = sum(f.charges for f in fills)

            # Running max drawdown
            if equity > self._peak_equity:
                self._peak_equity = equity
            if self._peak_equity > 0:
                dd_pct = (equity - self._peak_equity) / self._peak_equity * 100
                self._max_drawdown_pct = min(self._max_drawdown_pct, dd_pct)

    def render(self) -> None:
        """Print the dashboard to the terminal."""
        if not _RICH_AVAILABLE:
            self._render_plain()
            return

        with self._lock:
            self._render_rich()

    # ── Rich rendering ─────────────────────────────────────────────────────────

    def _render_rich(self) -> None:
        console = self._console
        console.clear()

        # ── Header ──────────────────────────────────────────────────────────────
        status_color = "green" if self._status == "RUNNING" else "yellow"
        header = Text()
        header.append(f" {self._status} ", style=f"bold white on {status_color}")
        header.append(f"  {self.exchange}:{self.symbol}  ", style="bold cyan")
        header.append(f"Bar #{self._bar_num}  ", style="dim")
        header.append(f"{_ist_now()}", style="dim")
        console.print(Panel(header, title="[bold]AlgoTrader Live[/bold]", box=box.ROUNDED))

        # ── Positions table ──────────────────────────────────────────────────────
        pos_table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
        pos_table.add_column("Symbol",     style="cyan")
        pos_table.add_column("Qty",        justify="right")
        pos_table.add_column("Avg Price",  justify="right")
        pos_table.add_column("Mark Price", justify="right")
        pos_table.add_column("Unrealised", justify="right")
        pos_table.add_column("P&L %",      justify="right")

        for pos in self._positions:
            mark = self._mark_price or pos.average_price
            unreal = pos.unrealised_pnl(mark)
            pct = _pct_str(unreal, pos.cost_basis)
            color = "green" if unreal >= 0 else "red"
            pos_table.add_row(
                f"{pos.exchange.value}:{pos.symbol}",
                str(pos.quantity),
                f"₹{pos.average_price:,.2f}",
                f"₹{mark:,.2f}",
                Text(_pnl_str(unreal), style=color),
                Text(pct, style=color),
            )

        if not self._positions:
            pos_table.add_row("[dim]No open positions[/dim]", "", "", "", "", "")

        console.print(Panel(pos_table, title="[bold]Positions[/bold]", box=box.ROUNDED))

        # ── Equity summary ───────────────────────────────────────────────────────
        day_pnl = self._equity - self._initial_capital
        pnl_color = "green" if day_pnl >= 0 else "red"
        eq_text = Text()
        eq_text.append(f"Capital: ₹{self._initial_capital:,.0f}   ")
        eq_text.append(f"Equity: ₹{self._equity:,.0f}   ")
        eq_text.append(
            f"Day P&L: {_pnl_str(day_pnl)} {_pct_str(day_pnl, self._initial_capital)}",
            style=pnl_color,
        )
        eq_text.append(
            f"\nMax Drawdown: {self._max_drawdown_pct:.2f}%   "
            f"Charges Today: ₹{self._daily_charges:,.2f}",
            style="dim",
        )
        console.print(Panel(eq_text, title="[bold]Equity[/bold]", box=box.ROUNDED))

        # ── Recent fills ──────────────────────────────────────────────────────────
        fill_table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        fill_table.add_column("Time",    style="dim")
        fill_table.add_column("Symbol",  style="cyan")
        fill_table.add_column("Side",    justify="center")
        fill_table.add_column("Qty",     justify="right")
        fill_table.add_column("Price",   justify="right")
        fill_table.add_column("Charges", justify="right", style="dim")

        recent = list(reversed(self._fills[-self.max_fills_shown:]))
        for f in recent:
            ts = f.timestamp
            if hasattr(ts, "strftime"):
                time_str = ts.astimezone(_IST).strftime("%H:%M:%S")
            else:
                time_str = str(ts)
            side_text = Text(f.side.value, style="green" if f.side == OrderSide.BUY else "red")
            fill_table.add_row(
                time_str,
                f"{f.exchange.value}:{f.symbol}",
                side_text,
                str(f.quantity),
                f"₹{f.price:,.2f}",
                f"₹{f.charges:.2f}",
            )

        if not recent:
            fill_table.add_row("[dim]No fills yet[/dim]", "", "", "", "", "")

        console.print(
            Panel(fill_table, title=f"[bold]Recent Fills (last {self.max_fills_shown})[/bold]",
                  box=box.ROUNDED)
        )

    # ── Plain-text fallback ────────────────────────────────────────────────────

    def _render_plain(self) -> None:
        print(f"\n{'='*60}")
        print(f" AlgoTrader Live | {self.exchange}:{self.symbol} | Bar #{self._bar_num} | {_ist_now()}")
        print(f" Status: {self._status}")
        print(f"{'='*60}")

        print("\nPOSITIONS:")
        if self._positions:
            for pos in self._positions:
                mark = self._mark_price or pos.average_price
                unreal = pos.unrealised_pnl(mark)
                print(f"  {pos.symbol}: qty={pos.quantity} avg=₹{pos.average_price:.2f}"
                      f" mark=₹{mark:.2f} P&L={_pnl_str(unreal)}")
        else:
            print("  (none)")

        day_pnl = self._equity - self._initial_capital
        print(f"\nEQUITY: ₹{self._equity:,.0f}"
              f" | Day P&L: {_pnl_str(day_pnl)}"
              f" | Charges: ₹{self._daily_charges:.2f}")
        print(f"Max Drawdown: {self._max_drawdown_pct:.2f}%")

        print("\nRECENT FILLS:")
        recent = list(reversed(self._fills[-5:]))
        if recent:
            for f in recent:
                print(f"  {f.side.value} {f.quantity} {f.symbol} @ ₹{f.price:.2f}"
                      f" charges=₹{f.charges:.2f}")
        else:
            print("  (none)")
        print(f"{'='*60}")
