"""Multi-strategy live runner.

Runs multiple Pine Script strategies against the same live data feed,
sharing a single broker instance and risk manager.

Architecture
------------
    MultiStrategyRunner.run()
        └─ for each bar (clock-aligned):
               for each StrategyConfig:
                   ① parse (once at startup)
                   ② evaluate Pine Script → signals
                   ③ route signals through shared RiskManager → shared broker

Each strategy gets its own Pine Script AST (parsed once at startup) and its
own ``strategy_name`` tag on orders so fills are attributed correctly.  They
share the same broker and risk manager so portfolio-level checks (max
exposure, daily loss limit) are enforced across all strategies jointly.

Edge constraints
----------------
* Per-strategy error isolation: if one strategy's evaluation raises, that
  strategy is skipped for the current bar with a logged warning; other
  strategies continue.
* Bar series are accumulated per-strategy so each strategy sees a consistent
  growing series history.
* Signal deduplication: signals with the same (symbol, action, strategy_name)
  within ``dedup_window_secs`` (default 0 — no dedup in multi mode because
  each strategy is distinct) are forwarded as-is.
* Shutdown: all open orders are cancelled; open NRML positions are warned about.
"""

from __future__ import annotations

import logging
import signal
import time
from dataclasses import dataclass, field
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


@dataclass
class StrategyConfig:
    """One strategy slot in the multi-strategy runner."""
    name: str                  # Unique strategy name (used as order tag)
    pine_script: str           # Raw Pine Script source
    symbol: str
    exchange: Exchange
    # Parsed AST is filled in at startup
    _ast: Any = field(default=None, repr=False)
    # Per-strategy bar accumulator
    _bars: Dict[str, List[float]] = field(
        default_factory=lambda: {
            "open": [], "high": [], "low": [], "close": [], "volume": []
        },
        repr=False,
    )


class MultiStrategyRunner:
    """Clock-aligned live runner that evaluates N strategies per bar.

    Usage::

        strategies = [
            StrategyConfig("ma_cross", ma_pine, "RELIANCE", Exchange.NSE),
            StrategyConfig("rsi_mean", rsi_pine, "INFY",    Exchange.NSE),
        ]
        broker = PaperBroker(initial_capital=1_000_000)
        runner = MultiStrategyRunner(strategies, broker)
        runner.run()   # blocks until SIGINT/SIGTERM
    """

    def __init__(
        self,
        strategies: List[StrategyConfig],
        broker: IBroker,
        live_feed: Optional[LiveDataFeed] = None,
        risk_config: Optional[RiskConfig] = None,
        interval_mins: int = 1,
        dry_run: bool = False,
    ) -> None:
        if not strategies:
            raise ValueError("At least one StrategyConfig is required.")

        self._strategies = strategies
        self._broker = broker
        self._feed = live_feed
        self._risk_config = risk_config or RiskConfig()
        self._interval_mins = interval_mins
        self._dry_run = dry_run
        self._shutdown = False

        # Shared risk manager — enforces portfolio-level limits across all strategies
        self._risk_mgr = RiskManager(
            config=self._risk_config,
            capital=self._safe_capital(),
        )

        # Parse all scripts once at startup
        for sc in self._strategies:
            tokens = Lexer(sc.pine_script).tokenize()
            sc._ast = Parser(tokens).parse()
            log.info("Parsed strategy '%s' for %s:%s", sc.name, sc.exchange.value, sc.symbol)

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Start the multi-strategy event loop.  Blocks until shutdown."""
        signal.signal(signal.SIGINT,  self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

        log.info(
            "MultiStrategyRunner starting | %d strategies | interval=%dmin | dry_run=%s",
            len(self._strategies), self._interval_mins, self._dry_run,
        )

        bar_num = 0
        try:
            while not self._shutdown:
                self._interruptible_sleep(self._secs_to_next_bar())
                if self._shutdown:
                    break

                bar_num += 1
                log.info("[bar %d] %s", bar_num, datetime.now().isoformat(timespec="seconds"))
                self._tick()
        finally:
            self._graceful_shutdown()

    def push_bar(self, strategy_name: str, bar: Dict[str, float]) -> None:
        """Manually push a bar to a named strategy (used in tests / paper mode)."""
        for sc in self._strategies:
            if sc.name == strategy_name:
                for key in ("open", "high", "low", "close", "volume"):
                    sc._bars[key].append(bar.get(key, 0.0))
                return
        raise ValueError(f"Strategy '{strategy_name}' not found")

    def push_bar_all(self, bar: Dict[str, float]) -> None:
        """Push the same bar to every strategy (useful when all trade the same symbol)."""
        for sc in self._strategies:
            for key in ("open", "high", "low", "close", "volume"):
                sc._bars[key].append(bar.get(key, 0.0))

    # ── Per-bar logic ──────────────────────────────────────────────────────────

    def _tick(self) -> None:
        for sc in self._strategies:
            try:
                self._tick_strategy(sc)
            except StaleDataError as exc:
                log.error("Strategy '%s': stale data — %s — skipping", sc.name, exc)
            except Exception as exc:
                log.exception("Strategy '%s': bar error — %s — skipping", sc.name, exc)

    def _tick_strategy(self, sc: StrategyConfig) -> None:
        # Fetch bar from feed if available
        if self._feed is not None:
            from algotrader.data.instrument_master import InstrumentMaster
            master = InstrumentMaster()
            try:
                ikey = master.get_instrument_key(sc.symbol, sc.exchange.value)
            except Exception:
                ikey = sc.symbol
            bar = self._feed.get_latest_bar(ikey)
            if bar is None:
                log.warning("Strategy '%s': no bar data — skipping", sc.name)
                return
            self._feed.check_stale()
            for key in ("open", "high", "low", "close", "volume"):
                sc._bars[key].append(bar.get(key, 0.0))

        if not sc._bars["close"]:
            return

        bar_idx = len(sc._bars["close"]) - 1
        close = sc._bars["close"][bar_idx]

        ev = Evaluator(
            sc._bars,
            symbol=sc.symbol,
            exchange=sc.exchange,
            strategy_name=sc.name,
        )
        signals = ev.run(sc._ast)
        bar_signals = [s for s in signals if s.metadata.get("bar_index") == bar_idx]

        for sig in bar_signals:
            if self._dry_run:
                log.info(
                    "[DRY-RUN][%s] %s %s @ %.2f",
                    sc.name, sig.action.value, sig.symbol, sig.price or close,
                )
                continue
            self._route_signal(sig, close, {k: v[bar_idx] for k, v in sc._bars.items()})

    def _route_signal(self, signal, market_price: float, bar: Dict[str, Any]) -> None:
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
                        self._broker.submit_order(order, market_price=market_price, bar=bar)
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
        now = datetime.now()
        interval_secs = self._interval_mins * 60
        elapsed = (now.second + now.microsecond / 1_000_000) % interval_secs
        return max(interval_secs - elapsed, 0.1)

    def _interruptible_sleep(self, duration: float) -> None:
        deadline = time.time() + duration
        while time.time() < deadline and not self._shutdown:
            time.sleep(min(1.0, deadline - time.time()))

    def _safe_capital(self) -> float:
        try:
            return self._broker.available_capital or 1_000_000.0
        except Exception:
            return 1_000_000.0

    def _graceful_shutdown(self) -> None:
        log.info("MultiStrategyRunner: graceful shutdown…")
        try:
            for order in list(self._broker.get_open_orders()):
                try:
                    self._broker.cancel_order(order.order_id)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            positions = self._broker.get_positions()
            if positions:
                log.warning("%d open position(s) remain after shutdown:", len(positions))
                for p in positions:
                    log.warning("  %s:%s qty=%d avg=%.2f", p.exchange.value, p.symbol, p.quantity, p.average_price)
        except Exception:
            pass
        log.info("MultiStrategyRunner: shutdown complete.")

    def _on_signal(self, signum: int, frame: Any) -> None:
        log.info("Signal %d received — shutting down", signum)
        self._shutdown = True
