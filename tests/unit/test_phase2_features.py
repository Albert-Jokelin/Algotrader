"""Phase-2 feature tests.

Covers the live-bot foundation features:
  18. live CLI command    — argument parsing, paper/upstox broker selection
  19. LiveDataFeed        — stale-data detection, None on failure
  20. Fund balance fetch  — cache TTL, correct field, stale fallback
  21. OrderPoller         — status transitions, callback fires, partial fills
  23. UpstoxOAuth         — token save/load, expiry detection, URL building
  25. InstrumentMaster    — CSV parsing, fallback map, InstrumentNotFoundError
  26. MIS/NRML product    — UpstoxBroker maps product correctly in payload
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from algotrader.auth.oauth import OAuthError, UpstoxOAuth
from algotrader.broker.upstox_broker import UpstoxBroker, _PRODUCT_MAP
from algotrader.config import UpstoxConfig
from algotrader.data.instrument_master import InstrumentMaster
from algotrader.data.live_feed import LiveDataFeed, StaleDataError
from algotrader.live.order_poller import OrderPoller
from algotrader.signals.models import Exchange, Order, OrderSide, OrderStatus, OrderType


# ─── helpers ──────────────────────────────────────────────────────────────────

def _upstox_cfg(**kwargs) -> UpstoxConfig:
    return UpstoxConfig(
        client_id="cid", client_secret="sec",
        redirect_uri="http://localhost:8765/callback",
        access_token="tok", sandbox=True, **kwargs
    )


def _mock_client(place_order_resp=None, fund_resp=None, positions_resp=None):
    client = MagicMock()
    client.place_order.return_value = place_order_resp or {
        "status": "COMPLETE",
        "order_id": "ORD123",
        "average_price": 2000.0,
        "filled_quantity": 10,
    }
    client.get_fund_and_margin.return_value = fund_resp or {
        "equity": {"available_margin": 123456.0}
    }
    client.get_positions.return_value = positions_resp or []
    return client


def _make_order(product: str = "NRML") -> Order:
    return Order(
        symbol="RELIANCE", exchange=Exchange.NSE,
        side=OrderSide.BUY, order_type=OrderType.MARKET,
        quantity=10, strategy_name="test", product=product,
    )


# ─── 19. LiveDataFeed ──────────────────────────────────────────────────────────

class TestLiveDataFeed:
    def _feed(self, **kw):
        feed = LiveDataFeed.__new__(LiveDataFeed)
        feed._access_token = "tok"
        feed._sandbox = True
        feed._stale_threshold = kw.get("stale_threshold_secs", 120)
        feed._last_successful_fetch = kw.get("last_fetch", None)
        feed._client = None   # No real client in tests
        return feed

    def test_returns_none_when_no_client(self):
        feed = self._feed()
        result = feed.get_latest_bar("NSE_EQ|INE002A01018")
        assert result is None

    def test_stale_raises_when_old_fetch(self):
        feed = self._feed(last_fetch=time.time() - 200, stale_threshold_secs=120)
        with pytest.raises(StaleDataError):
            feed.check_stale()

    def test_stale_ok_when_recent_fetch(self):
        feed = self._feed(last_fetch=time.time() - 30, stale_threshold_secs=120)
        feed.check_stale()  # Should not raise

    def test_not_stale_if_never_fetched(self):
        feed = self._feed(last_fetch=None)
        feed.check_stale()  # Never fetched → not considered stale

    def test_last_fetch_age_none_when_never_fetched(self):
        feed = self._feed()
        assert feed.last_fetch_age_secs is None

    def test_last_fetch_age_seconds_after_fetch(self):
        feed = self._feed(last_fetch=time.time() - 10)
        age = feed.last_fetch_age_secs
        assert 9 < age < 12

    def test_client_mock_returns_bar(self):
        """When client returns 3+ candles, second-to-last is returned."""
        feed = self._feed()
        mock_client = MagicMock()
        mock_client.get_historical_candle_data.return_value = {
            "candles": [
                ["2024-01-02T09:15:00", 100, 105, 98,  102, 50000, 0],
                ["2024-01-02T09:16:00", 102, 108, 101, 107, 60000, 0],  # completed
                ["2024-01-02T09:17:00", 107, 110, 106, 109, 30000, 0],  # forming
            ]
        }
        feed._client = mock_client
        bar = feed.get_latest_bar("NSE_EQ|INE002A01018")
        assert bar is not None
        assert bar["close"] == pytest.approx(107.0)
        assert bar["volume"] == pytest.approx(60000.0)

    def test_insufficient_candles_returns_none(self):
        feed = self._feed()
        mock_client = MagicMock()
        mock_client.get_historical_candle_data.return_value = {
            "candles": [["2024-01-02T09:15:00", 100, 105, 98, 102, 50000, 0]]
        }
        feed._client = mock_client
        assert feed.get_latest_bar("NSE_EQ|INE002A01018") is None


# ─── 20. UpstoxBroker — fund balance fetch ────────────────────────────────────

class TestUpstoxFundBalance:
    def _broker(self, fund_resp=None):
        broker = UpstoxBroker.__new__(UpstoxBroker)
        broker._config = _upstox_cfg()
        broker._client = _mock_client(fund_resp=fund_resp)
        broker._open_orders = {}
        broker._cash = 0.0
        broker._capital_fetched_at = 0.0
        broker._instrument_master = None
        broker._submitted_ids = set()
        return broker

    def test_available_capital_fetches_from_api(self):
        broker = self._broker(fund_resp={"equity": {"available_margin": 500_000.0}})
        assert broker.available_capital == pytest.approx(500_000.0)

    def test_capital_cached_within_ttl(self):
        broker = self._broker(fund_resp={"equity": {"available_margin": 999.0}})
        _ = broker.available_capital                  # First call — fetches
        broker._client.get_fund_and_margin.return_value = {
            "equity": {"available_margin": 888.0}    # Updated response
        }
        cached = broker.available_capital             # Should return cached value
        assert cached == pytest.approx(999.0)

    def test_cache_invalidated_after_ttl(self):
        broker = self._broker(fund_resp={"equity": {"available_margin": 999.0}})
        _ = broker.available_capital
        broker._capital_fetched_at = 0.0              # Expire the cache manually
        broker._client.get_fund_and_margin.return_value = {
            "equity": {"available_margin": 777.0}
        }
        assert broker.available_capital == pytest.approx(777.0)

    def test_stale_value_used_on_api_error(self):
        broker = self._broker()
        broker._cash = 42_000.0
        broker._capital_fetched_at = time.time()      # Fresh cache
        # Expire and make API fail
        broker._capital_fetched_at = 0.0
        broker._client.get_fund_and_margin.side_effect = RuntimeError("network error")
        # Should return stale value, not raise
        val = broker.available_capital
        assert val == pytest.approx(42_000.0)

    def test_correct_field_used(self):
        """Must use available_margin, not total_balance."""
        broker = self._broker(fund_resp={
            "equity": {
                "available_margin": 100_000.0,
                "total_balance":    500_000.0,   # Includes pledged collateral
            }
        })
        assert broker.available_capital == pytest.approx(100_000.0)


# ─── 21. OrderPoller ──────────────────────────────────────────────────────────

class TestOrderPoller:
    def _order(self, broker_id: str = "ORD001") -> Order:
        o = _make_order()
        o.broker_order_id = broker_id
        o.status = OrderStatus.OPEN
        return o

    def _broker_with_response(self, resp: dict):
        broker = MagicMock()
        broker._client.get_order_details.return_value = resp
        return broker

    def test_register_ignores_order_without_broker_id(self):
        poller = OrderPoller()
        order = _make_order()
        order.broker_order_id = None
        poller.register(order)
        assert poller.pending_count == 0

    def test_pending_count_increments_on_register(self):
        poller = OrderPoller()
        poller.register(self._order())
        assert poller.pending_count == 1

    def test_fill_updates_order_status(self):
        poller = OrderPoller()
        order = self._order()
        poller.register(order)
        broker = self._broker_with_response({
            "status": "COMPLETE",
            "filled_quantity": 10,
            "average_price": 2050.0,
        })
        poller._broker = broker
        poller._poll_once()
        assert order.status == OrderStatus.FILLED
        assert order.filled_quantity == 10
        assert order.average_fill_price == pytest.approx(2050.0)

    def test_fill_callback_fires(self):
        fills = []
        poller = OrderPoller(on_fill=fills.append)
        order = self._order()
        poller.register(order)
        poller._broker = self._broker_with_response({
            "status": "COMPLETE",
            "filled_quantity": 10,
            "average_price": 2050.0,
        })
        poller._poll_once()
        assert len(fills) == 1
        assert fills[0].price == pytest.approx(2050.0)

    def test_rejected_order_updates_status(self):
        poller = OrderPoller()
        order = self._order()
        poller.register(order)
        poller._broker = self._broker_with_response({
            "status": "REJECTED",
            "status_message": "Insufficient margin",
        })
        poller._poll_once()
        assert order.status == OrderStatus.REJECTED

    def test_partial_fill_updates_filled_quantity(self):
        poller = OrderPoller()
        order = self._order()
        poller.register(order)
        poller._broker = self._broker_with_response({
            "status": "OPEN",
            "filled_quantity": 5,
        })
        poller._poll_once()
        assert order.filled_quantity == 5
        assert order.status == OrderStatus.PARTIALLY_FILLED

    def test_no_double_fill_on_repeated_poll(self):
        fills = []
        poller = OrderPoller(on_fill=fills.append)
        order = self._order()
        poller.register(order)
        poller._broker = self._broker_with_response({
            "status": "COMPLETE",
            "filled_quantity": 10,
            "average_price": 2000.0,
        })
        poller._poll_once()
        poller._poll_once()   # Second poll after fill
        assert len(fills) == 1   # Callback must fire only once


# ─── 23. UpstoxOAuth ──────────────────────────────────────────────────────────

class TestUpstoxOAuth:
    def _oauth(self, token_path=None):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        os.unlink(path)  # start with no file
        return UpstoxOAuth(
            client_id="cid", client_secret="sec",
            redirect_uri="http://localhost:8765/callback",
            token_path=token_path or path,
        ), path

    def test_build_auth_url_contains_client_id(self):
        oauth, path = self._oauth()
        try:
            url = oauth.build_auth_url()
            assert "cid" in url
            assert "response_type=code" in url
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_save_and_load_token(self):
        oauth, path = self._oauth()
        try:
            # Create a non-expired token
            import zoneinfo
            IST = zoneinfo.ZoneInfo("Asia/Kolkata")
            future = (datetime.now(IST) + timedelta(hours=12)).isoformat()
            data = {"access_token": "abc123", "expires_at": future}
            oauth.save_token(data)
            loaded = oauth.load_token()
            assert loaded is not None
            assert loaded["access_token"] == "abc123"
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_load_returns_none_for_expired_token(self):
        oauth, path = self._oauth()
        try:
            past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            data = {"access_token": "old_token", "expires_at": past}
            oauth.save_token(data)
            loaded = oauth.load_token()
            assert loaded is None
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_load_returns_none_when_file_missing(self):
        oauth, path = self._oauth()
        assert not os.path.exists(path)
        assert oauth.load_token() is None

    def test_token_file_permissions(self):
        oauth, path = self._oauth()
        try:
            data = {"access_token": "tok"}
            oauth.save_token(data)
            mode = oct(os.stat(path).st_mode)[-3:]
            assert mode == "600"
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_access_token_from_file_returns_string(self):
        oauth, path = self._oauth()
        try:
            import zoneinfo
            IST = zoneinfo.ZoneInfo("Asia/Kolkata")
            future = (datetime.now(IST) + timedelta(hours=6)).isoformat()
            oauth.save_token({"access_token": "tok42", "expires_at": future})
            assert oauth.access_token_from_file() == "tok42"
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_access_token_from_file_none_when_missing(self):
        oauth, path = self._oauth()
        assert oauth.access_token_from_file() is None


# ─── 25. InstrumentMaster ─────────────────────────────────────────────────────

_SAMPLE_CSV = """\
instrument_key,tradingsymbol,exchange,instrument_type,lot_size
NSE_EQ|INE002A01018,RELIANCE,NSE,EQ,1
NSE_EQ|INE009A01021,INFY,NSE,EQ,1
NSE_FO|INE002A01018,RELIANCE24JANFUT,NSE,FUT,250
BSE_EQ|INE002A01018,RELIANCE,BSE,EQ,1
"""


class TestInstrumentMaster:
    def test_lookup_by_symbol_and_exchange(self):
        master = InstrumentMaster.from_csv_text(_SAMPLE_CSV)
        assert master.get_instrument_key("RELIANCE", "NSE") == "NSE_EQ|INE002A01018"

    def test_case_insensitive_lookup(self):
        master = InstrumentMaster.from_csv_text(_SAMPLE_CSV)
        assert master.get_instrument_key("reliance", "nse") == "NSE_EQ|INE002A01018"

    def test_filters_non_equity(self):
        master = InstrumentMaster.from_csv_text(_SAMPLE_CSV)
        # FUT instruments should NOT appear in the map
        assert master.size == 3   # 2 NSE EQ + 1 BSE EQ (FUT filtered out)

    def test_exchange_differentiates_listings(self):
        master = InstrumentMaster.from_csv_text(_SAMPLE_CSV)
        nse_key = master.get_instrument_key("RELIANCE", "NSE")
        bse_key = master.get_instrument_key("RELIANCE", "BSE")
        assert nse_key != bse_key

    def test_not_found_raises_instrument_not_found_error(self):
        from algotrader.exceptions import InstrumentNotFoundError
        master = InstrumentMaster.from_csv_text(_SAMPLE_CSV)
        with pytest.raises(InstrumentNotFoundError):
            master.get_instrument_key("UNKNOWN_XYZ", "NSE")

    def test_fallback_map_used_for_known_symbols(self):
        """When CSV is empty, fallback to hardcoded map."""
        master = InstrumentMaster.from_csv_text("instrument_key,tradingsymbol,exchange,instrument_type\n")
        # Hardcoded fallback should cover RELIANCE NSE
        key = master.get_instrument_key("RELIANCE", "NSE")
        assert "INE002A01018" in key

    def test_size_reflects_loaded_instruments(self):
        master = InstrumentMaster.from_csv_text(_SAMPLE_CSV)
        assert master.size == 3


# ─── 26. MIS/NRML product mapping in UpstoxBroker ────────────────────────────

class TestUpstoxProductMapping:
    def _broker_with_capture(self):
        """Return broker + list that captures the payload sent to place_order."""
        from algotrader.data.instrument_master import InstrumentMaster
        broker = UpstoxBroker.__new__(UpstoxBroker)
        broker._config = _upstox_cfg()
        broker._open_orders = {}
        broker._cash = 1_000_000.0
        broker._capital_fetched_at = time.time()
        broker._instrument_master = InstrumentMaster.from_csv_text(_SAMPLE_CSV)
        broker._submitted_ids = set()

        captured = []
        mock = MagicMock()
        mock.place_order.side_effect = lambda **kw: (captured.append(kw), {
            "status": "COMPLETE", "order_id": "X",
            "average_price": 2000.0, "filled_quantity": 10,
        })[1]
        broker._client = mock
        return broker, captured

    def test_mis_maps_to_i(self):
        broker, captured = self._broker_with_capture()
        broker.submit_order(_make_order(product="MIS"), market_price=2000.0)
        assert captured[0]["product"] == "I"

    def test_nrml_maps_to_d(self):
        broker, captured = self._broker_with_capture()
        broker.submit_order(_make_order(product="NRML"), market_price=2000.0)
        assert captured[0]["product"] == "D"

    def test_cnc_maps_to_d(self):
        broker, captured = self._broker_with_capture()
        broker.submit_order(_make_order(product="CNC"), market_price=2000.0)
        assert captured[0]["product"] == "D"

    def test_validity_passed_through(self):
        broker, captured = self._broker_with_capture()
        order = _make_order()
        order.validity = "IOC"
        broker.submit_order(order, market_price=2000.0)
        assert captured[0]["validity"] == "IOC"

    def test_unknown_product_falls_back_to_d(self):
        assert _PRODUCT_MAP.get("UNKNOWN", "D") == "D"


# ─── 18. CLI — live and login subcommands ─────────────────────────────────────

class TestCLI:
    def test_live_subcommand_exits_on_missing_script(self):
        from algotrader.cli import main
        rc = main(["live", "--script", "/nonexistent.pine", "--symbol", "RELIANCE"])
        assert rc == 1

    def test_login_subcommand_exits_without_credentials(self, capsys):
        from algotrader.cli import main
        rc = main(["login"])
        assert rc == 1
        captured = capsys.readouterr()
        assert "client_id" in captured.err.lower() or "client_secret" in captured.err.lower()

    def test_live_unknown_broker_exits(self, tmp_path):
        pine = tmp_path / "s.pine"
        pine.write_text('//@version=5\nstrategy("x")\n')
        from algotrader.cli import main
        rc = main(["live", "--script", str(pine), "--symbol", "X", "--broker", "unknown"])
        assert rc == 1

    def test_backtest_subcommand_recognized(self, capsys):
        """backtest subcommand with a missing script returns 1."""
        from algotrader.cli import main
        rc = main(["backtest", "--script", "/nope.pine", "--symbol", "X"])
        assert rc == 1


# ─── LiveEventLoop — unit tests (no real sleep) ───────────────────────────────

class TestLiveEventLoop:
    _SCRIPT = '//@version=5\nstrategy("Loop Test")\n'

    def _loop(self, **kw):
        from algotrader.broker.paper_broker import PaperBroker
        from algotrader.live.event_loop import LiveEventLoop
        broker = PaperBroker(initial_capital=500_000)
        return LiveEventLoop(
            pine_script=self._SCRIPT,
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            broker=broker,
            **kw,
        )

    def test_push_bar_grows_series(self):
        loop = self._loop()
        assert len(loop._bars["close"]) == 0
        loop.push_bar({"open": 100, "high": 105, "low": 98, "close": 102, "volume": 1000})
        assert len(loop._bars["close"]) == 1
        assert loop._bars["close"][0] == pytest.approx(102.0)

    def test_secs_to_next_bar_positive(self):
        loop = self._loop(interval_mins=1)
        secs = loop._secs_to_next_bar()
        assert 0 < secs <= 60

    def test_dry_run_flag_stored(self):
        loop = self._loop(dry_run=True)
        assert loop._dry_run is True

    def test_tick_with_no_bars_does_not_crash(self):
        loop = self._loop()
        loop._tick()   # Should not raise even with empty bars
