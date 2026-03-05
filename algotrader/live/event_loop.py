"""Live trading event loop.

Drives a Pine Script strategy against real-time market data, one completed
bar at a time.

Architecture
------------
    LiveEventLoop.run()
        └─ for each bar:
               ① sleep until bar boundary (clock-aligned)
               ② fetch latest completed bar via LiveDataFeed
               ③ check stale-data condition
               ④ process any queued open orders (via broker)
               ⑤ evaluate Pine Script → collect signals
               ⑥ route signals through RiskManager → broker

Edge constraints
----------------
* File lock (/tmp/algotrader.lock) prevents two instances running at once.
* Bar alignment: sleeps until the next multiple-of-interval minute boundary
  so bars align with the exchange clock (e.g. 09:15, 09:16, … for 1-min bars).
* SIGINT / SIGTERM → graceful shutdown sequence:
    1. Cancel all pending open orders.
    2. Warn about open NRML positions (not auto-closed).
    3. Release lock and exit.
* --dry-run: full evaluation runs but broker.submit_order() is never called.
* Exception isolation: per-bar errors are caught, logged, and the loop continues.
* Market hours: warns if started outside 09:00–15:30 IST.
* Heartbeat: one log line per bar so you know the bot is alive.
"""

from __future__ import annotations

import fcntl
import logging
import os
import signal
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from algotrader.broker.base import IBroker
from algotrader.config import RiskConfig
from algotrader.data.live_feed import LiveDataFeed, StaleDataError
from algotrader.pine.evaluator import Evaluator
from algotrader.pine.lexer import Lexer
from algotrader.pine.parser import Parser
from algotrader.risk.manager import RiskManager
from algotrader.signals.models import (
    Exchange,
    Order,
    OrderSide,
    OrderType,
    SignalAction,
)

log = logging.getLogger(__name__)

_LOCK_PATH = "/tmp/algotrader.lock"


class LiveEventLoop:
    """Runs a Pine Script strategy live, bar-by-bar."""

    def __init__(
        self,
        pine_script: str,
        symbol: str,
        exchange: Exchange,
        broker: IBroker,
        live_feed: Optional[LiveDataFeed] = None,
        risk_config: Optional[RiskConfig] = None,
        interval_mins: int = 1,
        dry_run: bool = False,
    ) -> None:
        self._symbol = symbol
        self._exchange = exchange
        self._broker = broker
        self._feed = live_feed
        self._risk_config = risk_config or RiskConfig()
        self._interval_mins = interval_mins
        self._dry_run = dry_run

        # Parse script once at startup.
        tokens = Lexer(pine_script).tokenize()
        self._ast = Parser(tokens).parse()

        self._risk_mgr = RiskManager(
            config=self._risk_config,
            capital=self._safe_capital(),
        )

        # Accumulated bars (grows by one per iteration).
        self._bars: Dict[str, List[float]] = {
            "open": [], "high": [], "low": [], "close": [], "volume": []
        }

        self._shutdown = False
        self._lock_fh = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Start the event loop.  Blocks until SIGINT/SIGTERM or fatal error."""
        self._acquire_lock()
        self._install_signal_handlers()
        self._warn_if_outside_hours()

        log.info(
            "LiveEventLoop starting | %s:%s | interval=%dmin | dry_run=%s",
            self._exchange.value, self._symbol, self._interval_mins, self._dry_run,
        )

        bar_num = 0
        try:
            while not self._shutdown:
                wait_secs = self._secs_to_next_bar()
                log.debug("Next bar in %.1fs", wait_secs)
                self._interruptible_sleep(wait_secs)
                if self._shutdown:
                    break

                bar_num += 1
                log.info("[bar %d] %s", bar_num, datetime.now().isoformat(timespec="seconds"))

                try:
                    self._tick()
                except StaleDataError as exc:
                    log.error("Stale data: %s — skipping bar", exc)
                except Exception as exc:
                    log.exception("Bar %d error (continuing): %s", bar_num, exc)
        finally:
            self._graceful_shutdown()
            self._release_lock()

    def push_bar(self, bar: Dict[str, float]) -> None:
        """Append a bar to the internal series (used in tests and paper-live mode)."""
        for key in ("open", "high", "low", "close", "volume"):
            self._bars[key].append(bar.get(key, 0.0))

    # ── Per-bar logic ──────────────────────────────────────────────────────────

    def _tick(self) -> None:
        """One complete bar iteration: fetch → check stops → evaluate → route."""
        # ① Fetch bar from live feed (skip if no feed configured — test/paper mode).
        if self._feed is not None:
            from algotrader.data.instrument_master import InstrumentMaster
            master = InstrumentMaster()
            try:
                ikey = master.get_instrument_key(self._symbol, self._exchange.value)
            except Exception:
                ikey = self._symbol   # fallback

            bar = self._feed.get_latest_bar(ikey)
            if bar is None:
                log.warning("No bar data returned — skipping tick")
                return
            self._feed.check_stale()
            self.push_bar(bar)

        if not self._bars["close"]:
            return

        bar_idx = len(self._bars["close"]) - 1
        current_bar = {k: v[bar_idx] for k, v in self._bars.items()}
        close = current_bar["close"]

        # ② Process any queued open orders against the new bar.
        for order in list(self._broker.get_open_orders()):
            try:
                self._broker.process_open_order(order, current_bar)
            except Exception:
                pass

        # ③ Evaluate Pine Script.
        ev = Evaluator(
            self._bars,
            symbol=self._symbol,
            exchange=self._exchange,
            strategy_name="live",
        )
        signals = ev.run(self._ast)
        bar_signals = [s for s in signals if s.metadata.get("bar_index") == bar_idx]

        # ④ Route signals.
        for signal in bar_signals:
            if self._dry_run:
                log.info(
                    "[DRY-RUN] %s %s @ %.2f",
                    signal.action.value, signal.symbol, signal.price or close,
                )
                continue
            self._route_signal(signal, close, current_bar)

    def _route_signal(
        self,
        signal,
        market_price: float,
        bar: Dict[str, Any],
    ) -> None:
        try:
            if signal.is_exit:
                for pos in self._broker.get_positions():
                    if (
                        pos.symbol == signal.symbol
                        and pos.strategy_name == signal.strategy_name
                    ):
                        order = Order(
                            symbol=pos.symbol,
                            exchange=pos.exchange,
                            side=OrderSide.SELL if pos.is_long else OrderSide.BUY,
                            order_type=OrderType.MARKET,
                            quantity=abs(pos.quantity),
                            strategy_name=signal.strategy_name,
                        )
                        self._broker.submit_order(
                            order, market_price=market_price, bar=bar
                        )
                return

            decision = self._risk_mgr.evaluate(signal, self._broker.get_positions())
            if not decision.approved or decision.quantity <= 0:
                return

            order = Order(
                symbol=signal.symbol,
                exchange=signal.exchange,
                side=OrderSide.BUY if signal.action == SignalAction.BUY else OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=decision.quantity,
                strategy_name=signal.strategy_name,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
            )
            self._broker.submit_order(order, market_price=market_price, bar=bar)

        except Exception as exc:
            log.warning("Signal routing error: %s", exc)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _secs_to_next_bar(self) -> float:
        """Return seconds until the next interval-aligned bar boundary."""
        now = datetime.now()
        interval_secs = self._interval_mins * 60
        elapsed = (now.second + now.microsecond / 1_000_000) % interval_secs
        remaining = interval_secs - elapsed
        return max(remaining, 0.1)

    def _interruptible_sleep(self, duration: float) -> None:
        """Sleep for `duration` seconds, waking every second to check shutdown."""
        deadline = time.time() + duration
        while time.time() < deadline and not self._shutdown:
            time.sleep(min(1.0, deadline - time.time()))

    def _safe_capital(self) -> float:
        try:
            return self._broker.available_capital or 1_000_000.0
        except Exception:
            return 1_000_000.0

    def _warn_if_outside_hours(self) -> None:
        try:
            from datetime import date
            from algotrader.strategy.market_hours import is_market_open
            if not is_market_open(date.today()):
                log.warning(
                    "Started outside NSE market hours (09:15–15:30 IST on trading days)."
                )
        except Exception:
            pass

    # ── Graceful shutdown ──────────────────────────────────────────────────────

    def _graceful_shutdown(self) -> None:
        log.info("Graceful shutdown initiated…")

        # Cancel all pending orders.
        try:
            for order in list(self._broker.get_open_orders()):
                try:
                    self._broker.cancel_order(order.order_id)
                    log.info("Cancelled order %s", order.order_id)
                except Exception as exc:
                    log.warning("Could not cancel %s: %s", order.order_id, exc)
        except Exception:
            pass

        # Warn about remaining positions.
        try:
            positions = self._broker.get_positions()
            if positions:
                log.warning(
                    "%d open position(s) remain after shutdown:", len(positions)
                )
                for pos in positions:
                    log.warning(
                        "  %s:%s qty=%d avg=%.2f  [NRML positions carry overnight]",
                        pos.exchange.value, pos.symbol, pos.quantity, pos.average_price,
                    )
        except Exception:
            pass

        log.info("Shutdown complete.")

    # ── Signal / lock ──────────────────────────────────────────────────────────

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT,  self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

    def _on_signal(self, signum: int, frame: Any) -> None:
        log.info("Signal %d received — shutting down after current bar", signum)
        self._shutdown = True

    def _acquire_lock(self) -> None:
        self._lock_fh = open(_LOCK_PATH, "w")
        try:
            fcntl.flock(self._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            print(
                f"ERROR: Another AlgoTrader instance is already running.\n"
                f"If incorrect, delete {_LOCK_PATH} and retry.",
                file=sys.stderr,
            )
            sys.exit(1)

    def _release_lock(self) -> None:
        if self._lock_fh:
            try:
                fcntl.flock(self._lock_fh, fcntl.LOCK_UN)
                self._lock_fh.close()
                os.unlink(_LOCK_PATH)
            except Exception:
                pass
