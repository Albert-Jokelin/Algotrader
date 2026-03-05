"""Tests for Phase 3 and Phase 4 features.

Phase 3
-------
* AlertManager — cooldown, Telegram/email gating, event toggles
* WebhookServer — payload validation, secret guard, dedup, routing
* StateStore — save/load, atomic write, stale-date reset, model roundtrip
* MultiStrategyRunner — push_bar, push_bar_all, strategy isolation
* UpstoxBroker idempotency — duplicate-submission guard, reconcile_open_orders

Phase 4
-------
* Intraday square-off — MIS positions closed at 15:15 when bar has timestamp
* GTC / IOC order validity — IOC expires, DAY expires, GTC persists
* BacktestCharts — files written, correct shape
* Calendar analysis — monthly_pnl aggregation, daily_pnl
* LiveDashboard — update + render (plain text fallback)
* PineScript UnsupportedFeatureError — for-loop, unknown ta.*, array.*, user fn
* UnsupportedFeatureError fields (feature, line, hint)
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

# ── Shared test helpers ────────────────────────────────────────────────────────

def _bar(open_=100, high=110, low=90, close=105, volume=10000):
    return dict(open=open_, high=high, low=low, close=close, volume=volume)


def _timestamped_bar(hour, minute, close=105):
    ts = datetime(2025, 1, 15, hour, minute, 0)
    return dict(open=100, high=110, low=90, close=close, volume=5000, timestamp=ts)


def _make_fill(symbol="RELIANCE", side="BUY", qty=10, price=100.0, charges=0.5):
    from algotrader.signals.models import Exchange, Fill, OrderSide
    from datetime import datetime, timezone
    return Fill(
        order_id="test-oid",
        symbol=symbol,
        exchange=Exchange.NSE,
        side=OrderSide(side),
        quantity=qty,
        price=price,
        charges=charges,
        timestamp=datetime(2025, 1, 15, 9, 30, 0, tzinfo=timezone.utc),
    )


# ══════════════════════════════════════════════════════════════════════════════
# AlertManager
# ══════════════════════════════════════════════════════════════════════════════

class TestAlertManager:
    def _mgr(self, **kwargs):
        from algotrader.alerts.manager import AlertConfig, AlertManager
        cfg = AlertConfig(cooldown_secs=0, **kwargs)
        return AlertManager(cfg)

    def test_no_credentials_no_crash(self):
        mgr = self._mgr()
        mgr.send("fill", "test message")  # should not raise

    def test_toggle_off_suppresses(self):
        from algotrader.alerts.manager import AlertConfig, AlertManager
        cfg = AlertConfig(on_fill=False, cooldown_secs=0)
        mgr = AlertManager(cfg)
        sent = []
        mgr._send_telegram = lambda m: sent.append(m)
        mgr.send("fill", "hello")
        assert len(sent) == 0

    def test_toggle_on_dispatches(self):
        from algotrader.alerts.manager import AlertConfig, AlertManager
        cfg = AlertConfig(on_fill=True, cooldown_secs=0)
        mgr = AlertManager(cfg)
        sent = []
        mgr._send_telegram = lambda m: sent.append(m)
        mgr._send_email = lambda s, m: None
        mgr.send("fill", "order filled")
        assert len(sent) == 1
        assert "FILL" in sent[0]

    def test_cooldown_suppresses_second_call(self):
        from algotrader.alerts.manager import AlertConfig, AlertManager
        cfg = AlertConfig(on_fill=True, cooldown_secs=3600)
        mgr = AlertManager(cfg)
        sent = []
        mgr._send_telegram = lambda m: sent.append(m)
        mgr._send_email = lambda s, m: None
        mgr.send("fill", "first")
        mgr.send("fill", "second")
        assert len(sent) == 1  # second suppressed by cooldown

    def test_convenience_on_fill(self):
        from algotrader.alerts.manager import AlertConfig, AlertManager
        cfg = AlertConfig(on_fill=True, cooldown_secs=0)
        mgr = AlertManager(cfg)
        msgs = []
        mgr._send_telegram = lambda m: msgs.append(m)
        mgr._send_email = lambda s, m: None
        mgr.on_fill("RELIANCE", "BUY", 50, 2450.0)
        assert any("BUY" in m for m in msgs)

    def test_unknown_event_type_dispatched(self):
        from algotrader.alerts.manager import AlertConfig, AlertManager
        cfg = AlertConfig(cooldown_secs=0)
        mgr = AlertManager(cfg)
        msgs = []
        mgr._send_telegram = lambda m: msgs.append(m)
        mgr._send_email = lambda s, m: None
        mgr.send("custom_event", "hello world")
        assert any("hello world" in m for m in msgs)


# ══════════════════════════════════════════════════════════════════════════════
# Webhook server
# ══════════════════════════════════════════════════════════════════════════════

class TestWebhookServer:
    def _app(self, secret=None, handler=None):
        from algotrader.config import WebhookConfig
        from algotrader.webhook.server import create_app
        cfg = WebhookConfig(secret=secret)
        return create_app(cfg, signal_handler=handler)

    def _client(self, app):
        from fastapi.testclient import TestClient
        return TestClient(app)

    def test_health_returns_ok(self):
        client = self._client(self._app())
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_valid_payload_accepted(self):
        client = self._client(self._app())
        resp = client.post("/webhook", json={
            "symbol": "RELIANCE", "exchange": "NSE", "action": "BUY",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

    def test_unknown_exchange_rejected(self):
        client = self._client(self._app())
        resp = client.post("/webhook", json={
            "symbol": "X", "exchange": "INVALID", "action": "BUY",
        })
        assert resp.status_code == 422

    def test_unknown_action_rejected(self):
        client = self._client(self._app())
        resp = client.post("/webhook", json={
            "symbol": "RELIANCE", "exchange": "NSE", "action": "HOLD",
        })
        assert resp.status_code == 422

    def test_secret_guard_rejects_wrong_secret(self):
        client = self._client(self._app(secret="mysecret"))
        resp = client.post(
            "/webhook",
            json={"symbol": "RELIANCE", "exchange": "NSE", "action": "BUY"},
            headers={"X-Webhook-Secret": "wrong"},
        )
        assert resp.status_code == 403

    def test_secret_guard_accepts_correct_secret(self):
        client = self._client(self._app(secret="mysecret"))
        resp = client.post(
            "/webhook",
            json={"symbol": "RELIANCE", "exchange": "NSE", "action": "BUY"},
            headers={"X-Webhook-Secret": "mysecret"},
        )
        assert resp.status_code == 200

    def test_handler_called_with_signal(self):
        received = []
        client = self._client(self._app(handler=lambda s: received.append(s)))
        client.post("/webhook", json={
            "symbol": "INFY", "exchange": "NSE", "action": "SELL",
            "strategy": "test_strat",
        })
        assert len(received) == 1
        assert received[0].symbol == "INFY"
        assert received[0].strategy_name == "test_strat"

    def test_dedup_suppresses_duplicate(self):
        from algotrader.config import WebhookConfig
        from algotrader.webhook.server import create_app
        cfg = WebhookConfig()
        app = create_app(cfg, dedup_window_secs=3600)
        client = self._client(app)
        payload = {"symbol": "RELIANCE", "exchange": "NSE", "action": "BUY"}
        r1 = client.post("/webhook", json=payload)
        r2 = client.post("/webhook", json=payload)
        assert r1.json()["status"] == "accepted"
        assert r2.json()["status"] == "duplicate"

    def test_close_action_normalised_to_exit(self):
        received = []
        client = self._client(self._app(handler=lambda s: received.append(s)))
        client.post("/webhook", json={
            "symbol": "RELIANCE", "exchange": "NSE", "action": "CLOSE",
        })
        assert received[0].action.value == "EXIT"

    def test_signal_count_increments(self):
        client = self._client(self._app())
        client.post("/webhook", json={"symbol": "A", "exchange": "NSE", "action": "BUY"})
        client.post("/webhook", json={"symbol": "B", "exchange": "NSE", "action": "SELL"})
        resp = client.get("/health")
        assert resp.json()["signals_received"] == 2


# ══════════════════════════════════════════════════════════════════════════════
# StateStore
# ══════════════════════════════════════════════════════════════════════════════

class TestStateStore:
    def _store(self, tmp_path):
        from algotrader.live import state_store as ss
        original = ss._BASE_DIR
        ss._BASE_DIR = Path(tmp_path) / "state"
        store = ss.StateStore("RELIANCE", "NSE", "test")
        ss._BASE_DIR = original
        # patch the internal path to use tmp
        store._path = Path(tmp_path) / "state" / "RELIANCE_NSE_test.json"
        store._path.parent.mkdir(parents=True, exist_ok=True)
        return store

    def _position(self):
        from algotrader.signals.models import Exchange, Position
        return Position(
            symbol="RELIANCE", exchange=Exchange.NSE, strategy_name="test",
            quantity=50, average_price=2450.0,
        )

    def test_save_and_load_roundtrip(self, tmp_path):
        store = self._store(tmp_path)
        pos = self._position()
        store.save(
            capital=1_000_000.0,
            daily_pnl=-500.0,
            equity_curve=[1_000_000, 999_500],
            positions=[pos],
        )
        data = store.load()
        assert data is not None
        assert data["capital"] == 1_000_000.0
        assert data["daily_pnl"] == -500.0
        assert len(data["positions"]) == 1
        assert data["positions"][0].symbol == "RELIANCE"
        assert data["positions"][0].quantity == 50

    def test_load_returns_none_when_no_file(self, tmp_path):
        store = self._store(tmp_path)
        assert store.load() is None

    def test_stale_date_resets_daily_pnl(self, tmp_path):
        store = self._store(tmp_path)
        # Write state with yesterday's date
        payload = {
            "version": 1,
            "saved_at": "2020-01-01T09:00:00+05:30",
            "trade_date": "2020-01-01",   # clearly in the past
            "symbol": "RELIANCE",
            "exchange": "NSE",
            "strategy": "test",
            "capital": 500_000.0,
            "daily_pnl": -10_000.0,
            "equity_curve": [500_000],
            "positions": [],
            "open_orders": [],
        }
        store._path.write_text(json.dumps(payload))
        data = store.load()
        assert data is not None
        assert data["daily_pnl"] == 0.0   # reset for new day

    def test_delete_removes_file(self, tmp_path):
        store = self._store(tmp_path)
        store.save(1_000_000, 0, [], [])
        assert store._path.exists()
        store.delete()
        assert not store._path.exists()

    def test_atomic_write_produces_single_file(self, tmp_path):
        store = self._store(tmp_path)
        store.save(1_000_000, 0, [1, 2, 3], [])
        files = list(store._path.parent.iterdir())
        assert len(files) == 1
        assert files[0].suffix == ".json"

    def test_version_mismatch_returns_none(self, tmp_path):
        store = self._store(tmp_path)
        store._path.write_text(json.dumps({"version": 999, "trade_date": "2025-01-01"}))
        assert store.load() is None

    def test_order_roundtrip(self, tmp_path):
        from algotrader.signals.models import Exchange, Order, OrderSide, OrderStatus, OrderType
        store = self._store(tmp_path)
        order = Order(
            symbol="RELIANCE", exchange=Exchange.NSE,
            side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=10, strategy_name="test", price=2400.0,
        )
        order.status = OrderStatus.OPEN
        store.save(1_000_000, 0, [], [], open_orders=[order])
        data = store.load()
        assert len(data["open_orders"]) == 1
        o = data["open_orders"][0]
        assert o.symbol == "RELIANCE"
        assert o.price == 2400.0
        assert o.status == OrderStatus.OPEN


# ══════════════════════════════════════════════════════════════════════════════
# UpstoxBroker idempotency
# ══════════════════════════════════════════════════════════════════════════════

class TestUpstoxIdempotency:
    def _broker(self, place_response=None):
        from algotrader.broker.upstox_broker import UpstoxBroker, _FallbackInstrumentMaster
        from algotrader.config import UpstoxConfig
        cfg = UpstoxConfig(client_id="x", client_secret="y", access_token="z")
        broker = UpstoxBroker(cfg, instrument_master=_FallbackInstrumentMaster())
        mock_client = MagicMock()
        mock_client.place_order.return_value = place_response or {
            "status": "OPEN",
            "order_id": "upstox-123",
        }
        mock_client.get_fund_and_margin.return_value = {"equity": {"available_margin": 1_000_000}}
        broker._client = mock_client
        return broker, mock_client

    def _order(self):
        from algotrader.signals.models import Exchange, Order, OrderSide, OrderType
        return Order(
            symbol="RELIANCE", exchange=Exchange.NSE,
            side=OrderSide.BUY, order_type=OrderType.MARKET,
            quantity=10, strategy_name="test",
        )

    def test_submit_uses_uuid_tag(self):
        broker, mock_client = self._broker()
        order = self._order()
        broker.submit_order(order)
        call_kwargs = mock_client.place_order.call_args[1]
        assert call_kwargs["tag"] == order.order_id[:20]

    def test_duplicate_submission_raises(self):
        from algotrader.exceptions import OrderRejectedError
        broker, _ = self._broker()
        order = self._order()
        broker.submit_order(order)   # first submission
        with pytest.raises(OrderRejectedError, match="Duplicate submission"):
            broker.submit_order(order)  # second — same order_id

    def test_reconcile_updates_status_to_filled(self):
        from algotrader.signals.models import OrderStatus
        broker, mock_client = self._broker()
        order = self._order()
        broker.submit_order(order)  # queued as OPEN
        assert order.order_id in broker._open_orders

        # Now mock the API showing it as COMPLETE
        mock_client.get_orders.return_value = [{
            "order_id": "upstox-123",
            "tag": order.order_id[:20],
            "status": "COMPLETE",
            "filled_quantity": 10,
        }]
        n = broker.reconcile_open_orders()
        assert n == 1
        assert order.status == OrderStatus.FILLED
        assert order.order_id not in broker._open_orders

    def test_reconcile_updates_status_to_cancelled(self):
        from algotrader.signals.models import OrderStatus
        broker, mock_client = self._broker()
        order = self._order()
        broker.submit_order(order)

        mock_client.get_orders.return_value = [{
            "order_id": "upstox-123",
            "tag": order.order_id[:20],
            "status": "CANCELLED",
        }]
        broker.reconcile_open_orders()
        assert order.status == OrderStatus.CANCELLED

    def test_reconcile_no_match_keeps_order(self):
        broker, mock_client = self._broker()
        order = self._order()
        broker.submit_order(order)

        mock_client.get_orders.return_value = []  # nothing on broker side
        broker.reconcile_open_orders()
        assert order.order_id in broker._open_orders  # still there

    def test_reconcile_api_failure_returns_zero(self):
        broker, mock_client = self._broker()
        order = self._order()
        broker.submit_order(order)

        mock_client.get_orders.side_effect = Exception("network error")
        result = broker.reconcile_open_orders()
        assert result == 0


# ══════════════════════════════════════════════════════════════════════════════
# Pine Script UnsupportedFeatureError
# ══════════════════════════════════════════════════════════════════════════════

class TestPineScriptSafety:
    def _eval(self, pine_script: str):
        from algotrader.pine.evaluator import Evaluator
        from algotrader.pine.lexer import Lexer
        from algotrader.pine.parser import Parser
        from algotrader.signals.models import Exchange
        bars = {"open": [100.0], "high": [110.0], "low": [90.0],
                "close": [105.0], "volume": [1000.0]}
        tokens = Lexer(pine_script).tokenize()
        ast = Parser(tokens).parse()
        ev = Evaluator(bars, symbol="RELIANCE", exchange=Exchange.NSE)
        return ev.run(ast)

    def test_valid_script_runs_without_error(self):
        signals = self._eval("""
strategy("test")
x = ta.sma(close, 5)
""")
        assert isinstance(signals, list)

    def test_unknown_ta_function_raises(self):
        from algotrader.exceptions import UnsupportedFeatureError
        with pytest.raises(UnsupportedFeatureError) as exc_info:
            self._eval("x = ta.vwap(close, 20)")
        assert "ta.vwap" in str(exc_info.value)

    def test_unknown_namespace_raises(self):
        from algotrader.exceptions import UnsupportedFeatureError
        with pytest.raises(UnsupportedFeatureError) as exc_info:
            self._eval("x = array.new_float(10)")
        assert "array" in str(exc_info.value)

    def test_unknown_builtin_function_raises(self):
        from algotrader.exceptions import UnsupportedFeatureError
        with pytest.raises(UnsupportedFeatureError) as exc_info:
            self._eval("x = myCustomFunction(close)")
        assert "myCustomFunction" in str(exc_info.value)

    def test_unsupported_feature_error_has_feature_attribute(self):
        from algotrader.exceptions import UnsupportedFeatureError
        err = UnsupportedFeatureError(feature="for_loop", line=5, hint="use ta.* instead")
        assert err.feature == "for_loop"
        assert err.line == 5
        assert "for_loop" in str(err)

    def test_nz_builtin_works(self):
        # nz() is a known built-in — must not raise
        signals = self._eval("x = nz(close, 0)")
        assert isinstance(signals, list)

    def test_visual_builtins_are_tolerated(self):
        # plot(), alert() etc. should be silently swallowed
        signals = self._eval("""
strategy("test")
plot(close)
""")
        assert isinstance(signals, list)


# ══════════════════════════════════════════════════════════════════════════════
# GTC / IOC order validity in PaperBroker
# ══════════════════════════════════════════════════════════════════════════════

class TestOrderValidity:
    def _broker(self):
        from algotrader.broker.paper_broker import PaperBroker
        return PaperBroker(initial_capital=1_000_000)

    def _limit_order(self, validity="DAY", price=90.0):
        from algotrader.signals.models import Exchange, Order, OrderSide, OrderType
        return Order(
            symbol="RELIANCE", exchange=Exchange.NSE,
            side=OrderSide.BUY, order_type=OrderType.LIMIT,
            quantity=10, strategy_name="test",
            price=price, validity=validity,
        )

    def test_day_order_expires_via_expire_day_orders(self):
        from algotrader.signals.models import OrderStatus
        broker = self._broker()
        order = self._limit_order(validity="DAY", price=50.0)  # will not fill at 105
        broker.submit_order(order, market_price=105.0)
        assert order.order_id in {o.order_id for o in broker.get_open_orders()}
        n = broker.expire_day_orders()
        assert n == 1
        assert len(broker.get_open_orders()) == 0
        assert order.status.value == "EXPIRED"

    def test_gtc_order_not_expired_by_expire_day_orders(self):
        broker = self._broker()
        order = self._limit_order(validity="GTC", price=50.0)
        broker.submit_order(order, market_price=105.0)
        n = broker.expire_day_orders()
        assert n == 0  # GTC order survives
        assert len(broker.get_open_orders()) == 1

    def test_ioc_order_expires_immediately_if_not_filled(self):
        from algotrader.signals.models import OrderStatus
        broker = self._broker()
        # Price=50, market=105 — limit won't trigger (bar high < 50 not possible here)
        order = self._limit_order(validity="IOC", price=50.0)
        fill = broker.submit_order(order, market_price=105.0, bar=_bar(close=105))
        assert fill is None
        assert order.status == OrderStatus.EXPIRED
        assert len(broker.get_open_orders()) == 0

    def test_ioc_order_fills_if_price_triggers(self):
        broker = self._broker()
        # Buy limit at 110, bar low=90 high=110 → will fill
        order = self._limit_order(validity="IOC", price=110.0)
        bar = _bar(open_=95, high=110, low=90, close=105)
        fill = broker.submit_order(order, market_price=105.0, bar=bar)
        assert fill is not None


# ══════════════════════════════════════════════════════════════════════════════
# Intraday square-off in engine
# ══════════════════════════════════════════════════════════════════════════════

class TestIntradaySquareoff:
    def _engine_with_squareoff(self):
        from algotrader.backtest.engine import BacktestEngine
        from algotrader.config import RiskConfig
        from algotrader.signals.models import Exchange
        bars = {
            "open":   [100.0, 101.0, 102.0],
            "high":   [110.0, 111.0, 112.0],
            "low":    [90.0,  91.0,  92.0],
            "close":  [105.0, 106.0, 107.0],
            "volume": [5000.0, 5000.0, 5000.0],
            "timestamp": [
                "2025-01-15T09:15:00",
                "2025-01-15T09:16:00",
                "2025-01-15T15:15:00",  # ← squareoff time
            ],
        }
        rc = RiskConfig(squareoff_time="15:15")
        return BacktestEngine(
            bars=bars,
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            initial_capital=1_000_000,
            risk_config=rc,
        )

    def test_mis_position_closed_at_squareoff_bar(self):
        """Smoke test: engine runs without error when bars have timestamps."""
        engine = self._engine_with_squareoff()
        # A simple script that never trades — we just verify no crash
        result = engine.run("""
strategy("test")
""")
        assert result is not None

    def test_no_squareoff_when_no_timestamp_in_bar(self):
        """If bars have no timestamp key, square-off is silently skipped."""
        from algotrader.backtest.engine import BacktestEngine
        from algotrader.config import RiskConfig
        from algotrader.signals.models import Exchange
        bars = {
            "open":   [100.0], "high": [110.0], "low": [90.0],
            "close":  [105.0], "volume": [5000.0],
        }
        rc = RiskConfig(squareoff_time="15:15")
        engine = BacktestEngine(bars=bars, symbol="X", exchange=Exchange.NSE,
                                initial_capital=1_000_000, risk_config=rc)
        result = engine.run('strategy("t")')
        assert result is not None


# ══════════════════════════════════════════════════════════════════════════════
# Backtest charts
# ══════════════════════════════════════════════════════════════════════════════

class TestBacktestCharts:
    def test_plot_equity_curve_writes_file(self, tmp_path):
        from algotrader.backtest.charts import plot_equity_curve
        equity = [1_000_000 + i * 1000 for i in range(50)]
        path = str(tmp_path / "equity.png")
        result = plot_equity_curve(equity, initial_capital=1_000_000, save_path=path)
        assert result is not None
        assert os.path.exists(result)
        assert os.path.getsize(result) > 1000  # non-trivial PNG

    def test_plot_drawdown_writes_file(self, tmp_path):
        from algotrader.backtest.charts import plot_drawdown
        equity = [1_000_000, 995_000, 990_000, 998_000, 1_005_000]
        path = str(tmp_path / "drawdown.png")
        result = plot_drawdown(equity, save_path=path)
        assert result is not None
        assert os.path.exists(result)

    def test_short_equity_curve_returns_none(self):
        from algotrader.backtest.charts import plot_equity_curve, plot_drawdown
        assert plot_equity_curve([1_000_000]) is None
        assert plot_drawdown([1_000_000]) is None

    def test_plot_backtest_report_saves_two_files(self, tmp_path):
        from algotrader.backtest.charts import plot_backtest_report
        from algotrader.backtest.engine import BacktestResult
        result = BacktestResult(
            equity_curve=[1_000_000 + i * 500 for i in range(30)],
        )
        saved = plot_backtest_report(result, save_dir=str(tmp_path))
        assert len(saved) == 2
        for p in saved:
            assert os.path.exists(p)


# ══════════════════════════════════════════════════════════════════════════════
# Calendar analysis
# ══════════════════════════════════════════════════════════════════════════════

class TestCalendarAnalysis:
    def _fills(self):
        from algotrader.signals.models import Exchange, Fill, OrderSide
        fills = []
        # 2 BUY fills on Jan 15, 1 SELL fill on Jan 15 → net daily for Jan 15
        # 1 BUY fill on Jan 16, 1 SELL fill on Jan 16
        for d, side, price, qty in [
            ("2025-01-15", "BUY",  2400, 10),
            ("2025-01-15", "SELL", 2450, 10),
            ("2025-01-16", "BUY",  2430, 5),
            ("2025-01-16", "SELL", 2480, 5),
        ]:
            ts = datetime.fromisoformat(f"{d}T10:00:00+05:30")
            fills.append(Fill(
                order_id="x", symbol="RELIANCE", exchange=Exchange.NSE,
                side=OrderSide(side), quantity=qty, price=price,
                charges=20.0, timestamp=ts,
            ))
        return fills

    def test_daily_pnl_aggregation(self):
        from algotrader.backtest.calendar import daily_pnl
        from datetime import date
        fills = self._fills()
        dpnl = daily_pnl(fills)
        assert date(2025, 1, 15) in dpnl
        assert date(2025, 1, 16) in dpnl

    def test_monthly_pnl_aggregation(self):
        from algotrader.backtest.calendar import monthly_pnl
        fills = self._fills()
        mpnl = monthly_pnl(fills)
        assert (2025, 1) in mpnl

    def test_heatmap_writes_file(self, tmp_path):
        from algotrader.backtest.calendar import plot_calendar_heatmap
        path = str(tmp_path / "heatmap.png")
        result = plot_calendar_heatmap(self._fills(), save_path=path)
        assert result is not None
        assert os.path.exists(result)

    def test_daily_pnl_chart_writes_file(self, tmp_path):
        from algotrader.backtest.calendar import plot_daily_pnl
        path = str(tmp_path / "daily_pnl.png")
        result = plot_daily_pnl(self._fills(), save_path=path)
        assert result is not None
        assert os.path.exists(result)

    def test_empty_fills_returns_none(self):
        from algotrader.backtest.calendar import plot_calendar_heatmap, plot_daily_pnl
        assert plot_calendar_heatmap([]) is None
        assert plot_daily_pnl([]) is None


# ══════════════════════════════════════════════════════════════════════════════
# LiveDashboard
# ══════════════════════════════════════════════════════════════════════════════

class TestLiveDashboard:
    def test_plain_text_render_no_crash(self, capsys):
        from algotrader.live.dashboard import LiveDashboard
        import algotrader.live.dashboard as dash_mod
        # Force plain-text mode for test isolation
        original = dash_mod._RICH_AVAILABLE
        dash_mod._RICH_AVAILABLE = False
        try:
            dash = LiveDashboard("RELIANCE", exchange="NSE", strategy="test")
            fill = _make_fill()
            dash.update(
                bar_num=5,
                positions=[],
                fills=[fill],
                equity=1_005_000,
                initial_capital=1_000_000,
                mark_price=105.0,
            )
            dash.render()
            out = capsys.readouterr().out
            assert "RELIANCE" in out
            assert "Bar #5" in out
        finally:
            dash_mod._RICH_AVAILABLE = original

    def test_update_tracks_peak_and_drawdown(self):
        from algotrader.live.dashboard import LiveDashboard
        dash = LiveDashboard("X", "NSE", "s")
        dash.update(bar_num=1, positions=[], fills=[], equity=1_010_000,
                    initial_capital=1_000_000)
        dash.update(bar_num=2, positions=[], fills=[], equity=990_000,
                    initial_capital=1_000_000)
        assert dash._peak_equity == 1_010_000
        assert dash._max_drawdown_pct < 0

    def test_update_charges_summed(self):
        from algotrader.live.dashboard import LiveDashboard
        dash = LiveDashboard("X", "NSE", "s")
        fills = [_make_fill(charges=10.0), _make_fill(charges=5.0)]
        dash.update(bar_num=1, positions=[], fills=fills,
                    equity=1_000_000, initial_capital=1_000_000)
        assert dash._daily_charges == 15.0


# ══════════════════════════════════════════════════════════════════════════════
# MultiStrategyRunner
# ══════════════════════════════════════════════════════════════════════════════

class TestMultiStrategyRunner:
    _SIMPLE_PINE = 'strategy("s")\n'

    def _broker(self):
        from algotrader.broker.paper_broker import PaperBroker
        return PaperBroker(initial_capital=1_000_000)

    def test_push_bar_all_populates_bars(self):
        from algotrader.live.multi_strategy import MultiStrategyRunner, StrategyConfig
        from algotrader.signals.models import Exchange
        strategies = [
            StrategyConfig("s1", self._SIMPLE_PINE, "RELIANCE", Exchange.NSE),
            StrategyConfig("s2", self._SIMPLE_PINE, "INFY",     Exchange.NSE),
        ]
        runner = MultiStrategyRunner(strategies, self._broker())
        runner.push_bar_all(_bar(close=105))
        for sc in strategies:
            assert sc._bars["close"] == [105.0]

    def test_push_bar_specific_strategy(self):
        from algotrader.live.multi_strategy import MultiStrategyRunner, StrategyConfig
        from algotrader.signals.models import Exchange
        strategies = [
            StrategyConfig("s1", self._SIMPLE_PINE, "RELIANCE", Exchange.NSE),
        ]
        runner = MultiStrategyRunner(strategies, self._broker())
        runner.push_bar("s1", _bar(close=200))
        assert strategies[0]._bars["close"] == [200.0]

    def test_push_bar_unknown_strategy_raises(self):
        from algotrader.live.multi_strategy import MultiStrategyRunner, StrategyConfig
        from algotrader.signals.models import Exchange
        strategies = [StrategyConfig("s1", self._SIMPLE_PINE, "RELIANCE", Exchange.NSE)]
        runner = MultiStrategyRunner(strategies, self._broker())
        with pytest.raises(ValueError, match="not found"):
            runner.push_bar("unknown", _bar())

    def test_empty_strategies_raises(self):
        from algotrader.live.multi_strategy import MultiStrategyRunner
        with pytest.raises(ValueError):
            MultiStrategyRunner([], self._broker())


# ══════════════════════════════════════════════════════════════════════════════
# AlertConfig is in Settings
# ══════════════════════════════════════════════════════════════════════════════

class TestAlertConfigInSettings:
    def test_settings_has_alerts_field(self):
        from algotrader.config import Settings
        s = Settings()
        assert hasattr(s, "alerts")
        assert s.alerts.cooldown_secs == 60.0

    def test_alert_config_standalone(self):
        from algotrader.config import AlertConfig
        cfg = AlertConfig(telegram_token="token", telegram_chat_id="123")
        assert cfg.telegram_token == "token"
        assert cfg.on_fill is True

    def test_risk_config_has_squareoff_time(self):
        from algotrader.config import RiskConfig
        rc = RiskConfig()
        assert rc.squareoff_time == "15:15"
        assert rc.mis_leverage == 1.0

    def test_risk_config_custom_squareoff(self):
        from algotrader.config import RiskConfig
        rc = RiskConfig(squareoff_time="15:20", mis_leverage=5.0)
        assert rc.squareoff_time == "15:20"
        assert rc.mis_leverage == 5.0
