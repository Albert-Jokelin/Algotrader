"""Crash-recovery state persistence for the live trading bot.

Persists the bot's critical runtime state to a JSON file at the end of every
bar.  If the process crashes, the next startup reads the file and restores
positions, the equity curve, and the daily P&L instead of starting from a
clean slate.

File layout
-----------
~/.algotrader/state/<symbol>_<strategy>.json

Schema (JSON)::

    {
      "version":       1,
      "saved_at":      "2025-01-15T09:47:00+05:30",
      "trade_date":    "2025-01-15",
      "symbol":        "RELIANCE",
      "exchange":      "NSE",
      "strategy":      "ma_cross",
      "capital":       1000000.0,
      "daily_pnl":     -2500.0,
      "equity_curve":  [1000000, 998000, ...],
      "positions": [
        {
          "symbol":        "RELIANCE",
          "exchange":      "NSE",
          "strategy_name": "ma_cross",
          "quantity":      50,
          "average_price": 2450.0,
          "realised_pnl":  0.0,
          "trailing_pct":  null,
          "trailing_sl":   null,
          "hwm":           0.0
        }
      ],
      "open_orders": [
        { ... Order fields as dict ... }
      ]
    }

Edge constraints
----------------
* Atomic write: the file is written to a temp path first, then renamed.
  This prevents a partial/corrupt file if the process is killed mid-write.
* Stale-date detection: if the saved ``trade_date`` differs from today, the
  state is treated as a prior-session snapshot; positions are loaded but the
  daily P&L resets to zero (avoids yesterday's loss counting against today's
  limit).
* Thread-safe: save() acquires a lock so concurrent calls (unlikely but
  possible) do not interleave writes.
* Directory creation: ~/.algotrader/state/ is created with 0o700 permissions
  if it does not exist.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from algotrader.signals.models import Exchange, Order, OrderStatus, Position

log = logging.getLogger(__name__)

_STATE_VERSION = 1
_BASE_DIR = Path.home() / ".algotrader" / "state"


def _ist_now() -> str:
    """ISO-8601 timestamp in IST (+05:30)."""
    from datetime import timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).isoformat(timespec="seconds")


class StateStore:
    """Saves and restores live-bot runtime state between sessions.

    Usage::

        store = StateStore("RELIANCE", "NSE", "ma_cross")

        # At startup:
        state = store.load()
        if state:
            positions = state["positions"]
            daily_pnl = state["daily_pnl"]   # reset to 0 if trade_date differs

        # After each bar:
        store.save(
            capital=broker.available_capital,
            daily_pnl=risk_mgr.daily_pnl,
            equity_curve=equity_curve,
            positions=broker.get_positions(),
            open_orders=broker.get_open_orders(),
        )
    """

    def __init__(self, symbol: str, exchange: str, strategy: str) -> None:
        self._symbol = symbol
        self._exchange = exchange
        self._strategy = strategy
        self._path = _BASE_DIR / f"{symbol}_{exchange}_{strategy}.json"
        self._lock = threading.Lock()
        _BASE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            _BASE_DIR.chmod(0o700)
        except OSError:
            pass

    # ── Public API ─────────────────────────────────────────────────────────────

    def save(
        self,
        capital: float,
        daily_pnl: float,
        equity_curve: List[float],
        positions: List[Position],
        open_orders: Optional[List[Order]] = None,
    ) -> None:
        """Atomically persist current bot state to disk."""
        payload: Dict[str, Any] = {
            "version":       _STATE_VERSION,
            "saved_at":      _ist_now(),
            "trade_date":    date.today().isoformat(),
            "symbol":        self._symbol,
            "exchange":      self._exchange,
            "strategy":      self._strategy,
            "capital":       capital,
            "daily_pnl":     daily_pnl,
            "equity_curve":  list(equity_curve),
            "positions":     [_pos_to_dict(p) for p in positions],
            "open_orders":   [_order_to_dict(o) for o in (open_orders or [])],
        }

        with self._lock:
            try:
                # Write to a temp file in the same directory, then rename
                # (atomic on POSIX; near-atomic on Windows).
                dir_ = str(self._path.parent)
                fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        json.dump(payload, fh, indent=2)
                    os.replace(tmp_path, str(self._path))
                except Exception:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
            except Exception as exc:
                log.warning("StateStore.save failed: %s", exc)

    def load(self) -> Optional[Dict[str, Any]]:
        """Load persisted state from disk.

        Returns the state dict, or ``None`` if no state file exists.

        Stale-date handling
        -------------------
        If the saved ``trade_date`` differs from today, the loaded
        ``daily_pnl`` is reset to 0.0 and a warning is logged.  The positions
        and equity curve are still returned so the bot knows about open NRML
        (overnight) positions.
        """
        if not self._path.exists():
            return None

        try:
            with self._path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            log.warning("StateStore.load: could not read %s — %s", self._path, exc)
            return None

        if data.get("version", 0) != _STATE_VERSION:
            log.warning(
                "StateStore.load: incompatible state version %s (expected %d) — ignoring",
                data.get("version"), _STATE_VERSION,
            )
            return None

        saved_date = data.get("trade_date", "")
        today = date.today().isoformat()
        if saved_date != today:
            log.warning(
                "StateStore.load: saved date %s differs from today %s — "
                "resetting daily_pnl to 0.0 (positions preserved for NRML carry-over)",
                saved_date, today,
            )
            data["daily_pnl"] = 0.0

        # Deserialise positions and orders back to model objects.
        data["positions"] = [_dict_to_pos(d) for d in data.get("positions", [])]
        data["open_orders"] = [_dict_to_order(d) for d in data.get("open_orders", [])]

        log.info(
            "StateStore.load: restored %d position(s), %d open order(s) from %s",
            len(data["positions"]), len(data["open_orders"]), self._path.name,
        )
        return data

    def delete(self) -> None:
        """Remove the state file (call at clean session end)."""
        try:
            self._path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("StateStore.delete: %s", exc)

    @property
    def path(self) -> Path:
        return self._path


# ── Serialisation helpers ──────────────────────────────────────────────────────

def _pos_to_dict(p: Position) -> Dict[str, Any]:
    return {
        "symbol":        p.symbol,
        "exchange":      p.exchange.value,
        "strategy_name": p.strategy_name,
        "quantity":      p.quantity,
        "average_price": p.average_price,
        "realised_pnl":  p.realised_pnl,
        "trailing_pct":  p.trailing_pct,
        "trailing_sl":   p.trailing_sl,
        "hwm":           p.hwm,
    }


def _dict_to_pos(d: Dict[str, Any]) -> Position:
    try:
        exch = Exchange(d.get("exchange", "NSE"))
    except ValueError:
        exch = Exchange.NSE
    return Position(
        symbol=d["symbol"],
        exchange=exch,
        strategy_name=d.get("strategy_name", ""),
        quantity=int(d.get("quantity", 0)),
        average_price=float(d.get("average_price", 0.0)),
        realised_pnl=float(d.get("realised_pnl", 0.0)),
        trailing_pct=d.get("trailing_pct"),
        trailing_sl=d.get("trailing_sl"),
        hwm=float(d.get("hwm", 0.0)),
    )


def _order_to_dict(o: Order) -> Dict[str, Any]:
    return {
        "order_id":           o.order_id,
        "broker_order_id":    o.broker_order_id,
        "symbol":             o.symbol,
        "exchange":           o.exchange.value,
        "side":               o.side.value,
        "order_type":         o.order_type.value,
        "quantity":           o.quantity,
        "strategy_name":      o.strategy_name,
        "price":              o.price,
        "stop_price":         o.stop_price,
        "stop_loss":          o.stop_loss,
        "take_profit":        o.take_profit,
        "product":            o.product,
        "validity":           o.validity,
        "status":             o.status.value,
        "filled_quantity":    o.filled_quantity,
        "average_fill_price": o.average_fill_price,
        "signal_id":          o.signal_id,
    }


def _dict_to_order(d: Dict[str, Any]) -> Order:
    from algotrader.signals.models import OrderSide, OrderType
    try:
        exch = Exchange(d.get("exchange", "NSE"))
    except ValueError:
        exch = Exchange.NSE
    order = Order(
        symbol=d["symbol"],
        exchange=exch,
        side=OrderSide(d["side"]),
        order_type=OrderType(d["order_type"]),
        quantity=int(d["quantity"]),
        strategy_name=d.get("strategy_name", ""),
        price=d.get("price"),
        stop_price=d.get("stop_price"),
        stop_loss=d.get("stop_loss"),
        take_profit=d.get("take_profit"),
        product=d.get("product", "NRML"),
        validity=d.get("validity", "DAY"),
        signal_id=d.get("signal_id"),
    )
    # Restore IDs and status (bypass Pydantic validator by direct assignment)
    object.__setattr__(order, "order_id", d.get("order_id", order.order_id))
    object.__setattr__(order, "broker_order_id", d.get("broker_order_id"))
    object.__setattr__(order, "status", OrderStatus(d.get("status", "OPEN")))
    object.__setattr__(order, "filled_quantity", int(d.get("filled_quantity", 0)))
    object.__setattr__(order, "average_fill_price", d.get("average_fill_price"))
    return order
