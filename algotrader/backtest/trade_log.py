"""Trade log — FIFO-matched trade journal with CSV export.

Workflow
--------
1. Feed all fills from a backtest (or live session) into ``TradeLogger.record()``.
2. Call ``TradeLogger.to_csv(path)`` to write a per-trade CSV.

Each row in the CSV represents one round-trip trade matched on a FIFO basis
within a ``(symbol, exchange, strategy_name)`` group:
  - entry_time / exit_time  – Fill timestamps (IST where possible).
  - entry_price / exit_price
  - quantity
  - gross_pnl               – Price-difference P&L only.
  - entry_charges / exit_charges
  - net_pnl                 – gross_pnl minus all charges.
  - hold_fills              – Number of fills it took to open + close.
  - status                  – "CLOSED" | "OPEN" (position never exited).

Edge constraints respected
--------------------------
* FIFO: each BUY fill is paired with the earliest available SELL fill(s).
* Partial-exit handling: one entry can map to multiple exit fills (the same
  entry_time / entry_price row is repeated for each partial exit with the
  matched quantity).
* Open positions at the end of the log get status = "OPEN" with net_pnl = None.
* Entry/exit timestamps use IST timezone when the fill timestamp is tz-aware.
* The CSV is UTF-8 encoded. Pass ``excel_compat=True`` to prepend a BOM so
  Excel opens it correctly on Windows.
"""

from __future__ import annotations

import csv
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import timezone
from typing import Deque, Dict, List, Optional, Tuple

from algotrader.signals.models import Fill, OrderSide

try:
    import zoneinfo
    _IST = zoneinfo.ZoneInfo("Asia/Kolkata")
except ImportError:
    _IST = None  # Python < 3.9 without backport — fall back to UTC display


def _to_ist_str(ts) -> str:
    """Return an ISO-8601 string in IST, or the raw UTC string if unavailable."""
    if ts is None:
        return ""
    if _IST is not None and ts.tzinfo is not None:
        return ts.astimezone(_IST).isoformat(timespec="seconds")
    return ts.isoformat(timespec="seconds")


@dataclass
class _OpenEntry:
    """One FIFO entry on the buy-queue."""
    fill: Fill
    remaining_qty: int = 0

    def __post_init__(self):
        if self.remaining_qty == 0:
            self.remaining_qty = self.fill.quantity


@dataclass
class TradeRecord:
    """A matched (or still-open) round-trip trade."""
    symbol: str
    exchange: str
    strategy_name: str

    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    quantity: int

    gross_pnl: Optional[float]
    entry_charges: float
    exit_charges: float
    net_pnl: Optional[float]

    hold_fills: int
    status: str   # "CLOSED" | "OPEN"


class TradeLogger:
    """Matches fills into round-trip trades and exports them to CSV."""

    def __init__(self) -> None:
        # Key: (symbol, exchange, strategy_name) → FIFO deque of _OpenEntry
        self._buy_queues: Dict[Tuple[str, str, str], Deque[_OpenEntry]] = {}
        self._records: List[TradeRecord] = []

    # ── Public API ─────────────────────────────────────────────────────────────

    def record(self, fill: Fill, strategy_name: str = "backtest") -> None:
        """Process one fill and update the internal FIFO queues."""
        key = (fill.symbol, fill.exchange.value, strategy_name)

        if fill.side == OrderSide.BUY:
            if key not in self._buy_queues:
                self._buy_queues[key] = deque()
            self._buy_queues[key].append(_OpenEntry(fill=fill))
            return

        # SELL fill — match against pending BUY entries (FIFO)
        sell_qty_remaining = fill.quantity
        sell_charges_remaining = fill.charges
        q = self._buy_queues.get(key, deque())

        while sell_qty_remaining > 0 and q:
            entry = q[0]
            matched_qty = min(entry.remaining_qty, sell_qty_remaining)
            # Apportion charges proportionally to matched qty
            entry_charge_share = (
                entry.fill.charges * matched_qty / entry.fill.quantity
            )
            exit_charge_share = (
                sell_charges_remaining * matched_qty / fill.quantity
            )

            gross = (fill.price - entry.fill.price) * matched_qty
            net = gross - entry_charge_share - exit_charge_share

            self._records.append(
                TradeRecord(
                    symbol=fill.symbol,
                    exchange=fill.exchange.value,
                    strategy_name=strategy_name,
                    entry_time=_to_ist_str(entry.fill.timestamp),
                    entry_price=entry.fill.price,
                    exit_time=_to_ist_str(fill.timestamp),
                    exit_price=fill.price,
                    quantity=matched_qty,
                    gross_pnl=round(gross, 4),
                    entry_charges=round(entry_charge_share, 4),
                    exit_charges=round(exit_charge_share, 4),
                    net_pnl=round(net, 4),
                    hold_fills=2,
                    status="CLOSED",
                )
            )

            entry.remaining_qty -= matched_qty
            sell_qty_remaining -= matched_qty
            sell_charges_remaining -= exit_charge_share

            if entry.remaining_qty == 0:
                q.popleft()

    def flush_open_positions(self) -> None:
        """Mark any unmatched BUY entries as open (position never exited)."""
        for (symbol, exchange, strategy), q in self._buy_queues.items():
            for entry in q:
                if entry.remaining_qty <= 0:
                    continue
                self._records.append(
                    TradeRecord(
                        symbol=symbol,
                        exchange=exchange,
                        strategy_name=strategy,
                        entry_time=_to_ist_str(entry.fill.timestamp),
                        entry_price=entry.fill.price,
                        exit_time="",
                        exit_price=0.0,
                        quantity=entry.remaining_qty,
                        gross_pnl=None,
                        entry_charges=round(
                            entry.fill.charges * entry.remaining_qty / entry.fill.quantity,
                            4,
                        ),
                        exit_charges=0.0,
                        net_pnl=None,
                        hold_fills=1,
                        status="OPEN",
                    )
                )

    def to_csv(
        self,
        path: str,
        excel_compat: bool = False,
        force: bool = False,
    ) -> None:
        """Export matched trades to a CSV file.

        Args:
            path:         Destination file path.
            excel_compat: Prepend UTF-8 BOM for Excel on Windows.
            force:        Overwrite an existing file without raising.
        """
        if os.path.exists(path) and not force:
            raise FileExistsError(
                f"Trade log already exists: {path}  (use force=True to overwrite)"
            )

        self.flush_open_positions()

        mode = "w"
        encoding = "utf-8-sig" if excel_compat else "utf-8"

        with open(path, mode, newline="", encoding=encoding) as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=[
                    "symbol", "exchange", "strategy_name",
                    "entry_time", "entry_price",
                    "exit_time", "exit_price",
                    "quantity",
                    "gross_pnl", "entry_charges", "exit_charges", "net_pnl",
                    "hold_fills", "status",
                ],
            )
            writer.writeheader()
            for rec in self._records:
                writer.writerow(
                    {
                        "symbol":         rec.symbol,
                        "exchange":       rec.exchange,
                        "strategy_name":  rec.strategy_name,
                        "entry_time":     rec.entry_time,
                        "entry_price":    rec.entry_price,
                        "exit_time":      rec.exit_time,
                        "exit_price":     rec.exit_price if rec.status == "CLOSED" else "",
                        "quantity":       rec.quantity,
                        "gross_pnl":      rec.gross_pnl if rec.gross_pnl is not None else "",
                        "entry_charges":  rec.entry_charges,
                        "exit_charges":   rec.exit_charges,
                        "net_pnl":        rec.net_pnl if rec.net_pnl is not None else "",
                        "hold_fills":     rec.hold_fills,
                        "status":         rec.status,
                    }
                )

    @property
    def records(self) -> List[TradeRecord]:
        """All matched (and flushed-open) trade records."""
        return list(self._records)

    # ── Convenience factory ────────────────────────────────────────────────────

    @classmethod
    def from_fills(
        cls, fills: List[Fill], strategy_name: str = "backtest"
    ) -> "TradeLogger":
        """Build a TradeLogger from a list of fills in chronological order."""
        logger = cls()
        for fill in fills:
            logger.record(fill, strategy_name=strategy_name)
        return logger
