"""Event-driven backtesting engine.

Runs a Pine Script strategy against historical OHLCV data bar-by-bar:
  1. Parse the Pine Script.
  2. For each bar:
       a. Process queued open orders (limit, stop, partial fills) against the bar.
       b. Check automated stops (trailing SL, per-trade max loss) on open positions.
       c. Evaluate the Pine Script → collect signals for this bar.
       d. Route each signal through the risk manager → paper broker.
  3. Track equity curve (cash + open position mark-to-market value).
  4. Return a BacktestResult with fills, equity curve, and metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from algotrader.backtest.metrics import PerformanceMetrics
from algotrader.broker.paper_broker import PaperBroker
from algotrader.config import RiskConfig, Settings
from algotrader.pine.evaluator import Evaluator
from algotrader.pine.lexer import Lexer
from algotrader.pine.parser import Parser
from algotrader.risk.manager import RiskManager
from algotrader.signals.models import (
    Exchange,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    SignalAction,
    TradingSignal,
)


@dataclass
class BacktestResult:
    equity_curve: List[float] = field(default_factory=list)
    fills: List[Fill] = field(default_factory=list)
    metrics: Optional[PerformanceMetrics] = None
    n_signals: int = 0
    total_charges: float = 0.0


class BacktestEngine:
    """Runs a Pine Script strategy over historical bar data."""

    def __init__(
        self,
        bars: Dict[str, List[float]],
        symbol: str,
        exchange: Exchange,
        initial_capital: float = 1_000_000.0,
        risk_config: Optional[RiskConfig] = None,
    ) -> None:
        self._bars = bars
        self._symbol = symbol
        self._exchange = exchange
        self._initial_capital = initial_capital
        self._risk_config = risk_config or RiskConfig()
        self._n_bars = len(bars["close"])

    def run(self, pine_script: str) -> BacktestResult:
        """Execute the Pine Script strategy and return results."""
        # ── Parse script ──────────────────────────────────────────────────────
        tokens = Lexer(pine_script).tokenize()
        ast = Parser(tokens).parse()

        # ── Initialise broker and risk manager ────────────────────────────────
        broker = PaperBroker(
            initial_capital=self._initial_capital,
            risk_config=self._risk_config,
        )
        risk_mgr = RiskManager(
            config=self._risk_config, capital=self._initial_capital
        )

        equity_curve: List[float] = []
        all_fills: List[Fill] = []
        n_signals = 0

        # ── Bar-by-bar simulation ─────────────────────────────────────────────
        for bar_idx in range(self._n_bars):
            bar = self._bar_at(bar_idx)

            # (a) Process any queued open orders (limit/stop/partial) first
            self._process_open_orders(broker, risk_mgr, bar)

            # (b) Automated stop checks on open positions
            self._check_stops(broker, risk_mgr, bar)

            # (c) Evaluate Pine Script up to this bar
            bar_bars = self._bars_up_to(bar_idx)
            ev = Evaluator(
                bar_bars,
                symbol=self._symbol,
                exchange=self._exchange,
                strategy_name="backtest",
            )
            signals = ev.run(ast)

            # Only process signals tagged to this bar
            bar_signals = [
                s for s in signals if s.metadata.get("bar_index") == bar_idx
            ]
            n_signals += len(bar_signals)

            # (d) Route signals through risk manager → broker
            for signal in bar_signals:
                self._process_signal(signal, broker, risk_mgr, bar)

            all_fills = list(broker.fills)

            # Compute equity at this bar (cash + mark-to-market positions)
            close_price = bar["close"]
            equity = self._compute_equity(broker, close_price)
            equity_curve.append(equity)

        # ── Compute metrics ───────────────────────────────────────────────────
        metrics = PerformanceMetrics.compute(
            equity_curve=equity_curve,
            initial_capital=self._initial_capital,
            fills=all_fills,
        )

        return BacktestResult(
            equity_curve=equity_curve,
            fills=all_fills,
            metrics=metrics,
            n_signals=n_signals,
            total_charges=broker.total_charges,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _bar_at(self, bar_idx: int) -> Dict[str, Any]:
        """Return a single bar dict for bar_idx."""
        return {k: v[bar_idx] for k, v in self._bars.items()}

    def _bars_up_to(self, bar_idx: int) -> Dict[str, List[float]]:
        """Return bars slice up to and including bar_idx."""
        n = bar_idx + 1
        return {k: v[:n] for k, v in self._bars.items()}

    def _process_open_orders(
        self,
        broker: PaperBroker,
        risk_mgr: RiskManager,
        bar: Dict[str, Any],
    ) -> None:
        """Try to fill all queued open orders against the current bar."""
        for order in list(broker.get_open_orders()):
            fill = broker.process_open_order(order, bar)
            if fill and order.is_terminal:
                # If a position was closed, record the P&L for daily-loss tracking
                pos_key = f"{order.exchange.value}:{order.symbol}:{order.strategy_name}"
                if order.side == OrderSide.SELL:
                    risk_mgr.record_realised_pnl(fill.price * fill.quantity - fill.charges)

    def _check_stops(
        self,
        broker: PaperBroker,
        risk_mgr: RiskManager,
        bar: Dict[str, Any],
    ) -> None:
        """Check trailing SL and per-trade max loss for every open position.

        Takes a snapshot of positions first so that force-exits during
        iteration do not corrupt the loop.
        """
        bar_high  = bar.get("high",  bar["close"])
        bar_low   = bar.get("low",   bar["close"])
        bar_open  = bar.get("open",  bar["close"])
        bar_close = bar["close"]
        bar_volume = bar.get("volume", 0.0)

        for pos in list(broker.get_positions()):
            # ── Trailing stop-loss ─────────────────────────────────────────────
            if pos.trailing_pct is not None and pos.trailing_sl is not None:
                if pos.is_long:
                    # Advance high-water mark
                    if bar_high > pos.hwm:
                        pos.hwm = bar_high
                    # Raise trailing SL: only ever moves up
                    new_sl = pos.hwm * (1.0 - pos.trailing_pct)
                    if new_sl > pos.trailing_sl:
                        pos.trailing_sl = new_sl

                    # Check if the bar violated the trailing SL
                    if bar_low <= pos.trailing_sl:
                        # Gap-down scenario: fill at open if it's already past SL
                        fill_price = (
                            min(bar_open, pos.trailing_sl)
                            if bar_open < pos.trailing_sl
                            else pos.trailing_sl
                        )
                        self._force_exit(
                            pos, broker, risk_mgr, fill_price, bar_volume
                        )
                        continue

                elif pos.is_short:
                    # Advance low-water mark
                    if bar_low < pos.hwm or pos.hwm == 0.0:
                        pos.hwm = bar_low
                    # Lower trailing SL: only ever moves down for shorts
                    new_sl = pos.hwm * (1.0 + pos.trailing_pct)
                    if pos.trailing_sl == 0.0 or new_sl < pos.trailing_sl:
                        pos.trailing_sl = new_sl

                    if bar_high >= pos.trailing_sl:
                        fill_price = (
                            max(bar_open, pos.trailing_sl)
                            if bar_open > pos.trailing_sl
                            else pos.trailing_sl
                        )
                        self._force_exit(
                            pos, broker, risk_mgr, fill_price, bar_volume
                        )
                        continue

            # ── Per-trade max loss ─────────────────────────────────────────────
            max_loss_pct = self._risk_config.max_loss_per_trade_pct
            if max_loss_pct is not None:
                unrealised = pos.unrealised_pnl(bar_close)
                threshold = -(pos.cost_basis * max_loss_pct)
                if unrealised < threshold:
                    # Fill at close (conservative; gap-open scenarios handled
                    # on the next bar via the normal signal flow)
                    self._force_exit(
                        pos, broker, risk_mgr, bar_close, bar_volume
                    )

    def _force_exit(
        self,
        pos: Position,
        broker: PaperBroker,
        risk_mgr: RiskManager,
        fill_price: float,
        bar_volume: float = 0.0,
    ) -> None:
        """Submit a market exit order for an open position."""
        side = OrderSide.SELL if pos.is_long else OrderSide.BUY
        order = Order(
            symbol=pos.symbol,
            exchange=pos.exchange,
            side=side,
            order_type=OrderType.MARKET,
            quantity=abs(pos.quantity),
            strategy_name=pos.strategy_name,
        )
        bar_for_broker: Dict[str, Any] = {"volume": bar_volume}
        fill = broker.submit_order(order, market_price=fill_price, bar=bar_for_broker)
        if fill:
            gross_pnl = (fill.price - pos.average_price) * (
                abs(pos.quantity) if pos.is_long else -abs(pos.quantity)
            )
            risk_mgr.record_realised_pnl(gross_pnl - fill.charges)

    def _process_signal(
        self,
        signal: TradingSignal,
        broker: PaperBroker,
        risk_mgr: RiskManager,
        bar: Dict[str, Any],
    ) -> None:
        market_price = bar["close"]

        try:
            if signal.is_exit:
                # Close existing position for this symbol/strategy
                for pos in broker.get_positions():
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
                            signal_id=signal.signal_id,
                        )
                        fill = broker.submit_order(
                            order, market_price=market_price, bar=bar
                        )
                        if fill:
                            gross_pnl = (fill.price - pos.average_price) * abs(
                                pos.quantity
                            )
                            risk_mgr.record_realised_pnl(gross_pnl - fill.charges)
                return

            # Entry signal
            decision = risk_mgr.evaluate(signal, broker.get_positions())
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
                signal_id=signal.signal_id,
            )
            broker.submit_order(order, market_price=market_price, bar=bar)

        except Exception:
            # Swallow individual signal errors in backtest (log in production)
            pass

    def _compute_equity(self, broker: PaperBroker, close_price: float) -> float:
        """Total equity = cash + mark-to-market value of open positions."""
        equity = broker.available_capital
        for pos in broker.get_positions():
            equity += abs(pos.quantity) * close_price
        return equity
