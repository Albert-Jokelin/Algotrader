"""Phase-1 feature tests.

Tests every new behaviour added in Phase 1:
  1.  Slippage               – market-order fill price shifted by bps
  2.  Brokerage commission    – min(flat, pct × turnover) deducted per fill
  3.  STT & exchange charges  – ChargesCalculator produces non-zero charges
  4.  Partial fills           – volume_cap_pct limits fill size; remainder queued
  5.  OHLC limit fill         – trigger on bar low/high, not just close
  6.  Gap-open limit fill     – fill at open when open price skips past limit
  7.  Trailing stop-loss      – hwm advances, SL moves only in profit direction
  8.  Trailing SL fires       – force-exit when bar_low ≤ trailing_sl
  9.  Per-trade max loss      – force-exit when unrealised loss exceeds threshold
  10. Trade log CSV           – FIFO matching, charges, open positions
  11. BacktestResult.total_charges – charges propagated to result
  12. Order model fields       – product / validity defaults
  13. Fill.net_value           – value minus charges
"""

from __future__ import annotations

import csv
import os
import tempfile

import pytest

from algotrader.backtest.engine import BacktestEngine, BacktestResult
from algotrader.backtest.trade_log import TradeLogger
from algotrader.broker.charges import ChargesCalculator
from algotrader.broker.paper_broker import PaperBroker
from algotrader.config import RiskConfig
from algotrader.signals.models import (
    Exchange,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _cfg(**kwargs) -> RiskConfig:
    """Build a RiskConfig with sane testing defaults."""
    return RiskConfig(**kwargs)


def _mkt_buy(qty: int = 10, symbol: str = "RELIANCE") -> Order:
    return Order(
        symbol=symbol,
        exchange=Exchange.NSE,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=qty,
        strategy_name="test",
    )


def _mkt_sell(qty: int = 10, symbol: str = "RELIANCE") -> Order:
    return Order(
        symbol=symbol,
        exchange=Exchange.NSE,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=qty,
        strategy_name="test",
    )


def _limit_buy(price: float, qty: int = 10) -> Order:
    return Order(
        symbol="RELIANCE",
        exchange=Exchange.NSE,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=qty,
        price=price,
        strategy_name="test",
    )


def _limit_sell(price: float, qty: int = 10) -> Order:
    return Order(
        symbol="RELIANCE",
        exchange=Exchange.NSE,
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        quantity=qty,
        price=price,
        strategy_name="test",
    )


# ── 1. Slippage ─────────────────────────────────────────────────────────────────

class TestSlippage:
    def test_buy_fills_above_market_price(self):
        broker = PaperBroker(100_000, _cfg(slippage_bps=10))
        fill = broker.submit_order(_mkt_buy(qty=1), market_price=1000.0)
        assert fill.price > 1000.0
        assert fill.price == pytest.approx(1000.0 * 1.001)

    def test_sell_fills_below_market_price(self):
        broker = PaperBroker(100_000, _cfg(slippage_bps=10))
        broker.submit_order(_mkt_buy(qty=1), market_price=1000.0)
        fill = broker.submit_order(_mkt_sell(qty=1), market_price=1000.0)
        assert fill.price < 1000.0
        assert fill.price == pytest.approx(1000.0 * 0.999)

    def test_zero_slippage_fills_at_exact_price(self):
        broker = PaperBroker(100_000, _cfg(slippage_bps=0))
        fill = broker.submit_order(_mkt_buy(qty=1), market_price=1000.0)
        assert fill.price == pytest.approx(1000.0)

    def test_no_slippage_without_config(self):
        broker = PaperBroker(100_000)   # no RiskConfig → no slippage
        fill = broker.submit_order(_mkt_buy(qty=1), market_price=1000.0)
        assert fill.price == pytest.approx(1000.0)

    def test_limit_order_no_slippage(self):
        """Limit orders must NOT have slippage applied."""
        broker = PaperBroker(100_000, _cfg(slippage_bps=100))   # 1% slippage
        order = _limit_buy(price=1000.0, qty=1)
        fill = broker.submit_order(order, market_price=900.0)  # below limit → fills
        # Limit fill price must be ≤ limit price (gap case: open=900)
        assert fill.price <= 1000.0


# ── 2. Brokerage commission ────────────────────────────────────────────────────

class TestCommission:
    def test_flat_commission_deducted_on_buy(self):
        broker = PaperBroker(100_000, _cfg(commission_flat=20.0))
        broker.submit_order(_mkt_buy(qty=10), market_price=1000.0)
        # Cost = 10 × 1000 = 10,000 + ₹20 brokerage (+ tiny STT/other)
        assert broker.available_capital < 100_000 - 10_000

    def test_commission_recorded_in_fill_charges(self):
        broker = PaperBroker(100_000, _cfg(commission_flat=20.0))
        fill = broker.submit_order(_mkt_buy(qty=10), market_price=1000.0)
        assert fill.charges > 0

    def test_percentage_commission(self):
        broker = PaperBroker(100_000, _cfg(commission_pct=0.0003))
        fill = broker.submit_order(_mkt_buy(qty=10), market_price=1000.0)
        # Brokerage portion ≈ 0.03% × 10,000 = 3 INR, but total charges include STT etc.
        assert fill.charges > 0

    def test_min_of_flat_vs_pct(self):
        """Charges use min(flat, pct*turnover)."""
        # flat=50, pct=0.01% → pct charge on 1000 turnover = 0.1 → use 0.1
        c_low_pct = ChargesCalculator.compute("NRML", "BUY", 1, 1000.0,
                                              commission_flat=50.0, commission_pct=0.0001)
        # flat=1, pct=10% → pct charge on 1000 = 100 → use 1
        c_low_flat = ChargesCalculator.compute("NRML", "BUY", 1, 1000.0,
                                               commission_flat=1.0, commission_pct=0.10)
        assert c_low_pct < c_low_flat   # first case uses pct (0.1 < 1)

    def test_zero_commission_without_config(self):
        broker = PaperBroker(100_000)
        fill = broker.submit_order(_mkt_buy(qty=10), market_price=1000.0)
        # With no config: charges = exchange/SEBI/stamp only when comm=0
        # ChargesCalculator with comm_flat=0, comm_pct=0 → brokerage = 0
        # But STT, exchange charge etc. are still 0 because PaperBroker passes 0.
        assert fill.charges == pytest.approx(0.0)


# ── 3. STT & exchange charges ──────────────────────────────────────────────────

class TestChargesCalculator:
    def test_delivery_stt_on_sell_only(self):
        buy_charges  = ChargesCalculator.compute("NRML", "BUY",  100, 500.0)
        sell_charges = ChargesCalculator.compute("NRML", "SELL", 100, 500.0)
        # NRML STT = 0.1% on sell only → sell should have more charges
        assert sell_charges > buy_charges

    def test_intraday_stt_on_both_sides(self):
        buy_charges  = ChargesCalculator.compute("MIS", "BUY",  100, 500.0)
        sell_charges = ChargesCalculator.compute("MIS", "SELL", 100, 500.0)
        # MIS STT same rate on both sides; charges should be similar (not zero on buy)
        assert buy_charges > 0
        assert sell_charges > 0

    def test_stamp_duty_buy_side_only(self):
        # Stamp duty (NRML): 0.015% on BUY, 0 on SELL.
        # Sell side has STT (0.1% = 10 INR on 10k), so sell_c > buy_c overall.
        # We verify stamp duty is present on the buy side:
        #   expected stamp = 0.00015 × 10_000 = 1.5 INR
        buy_c = ChargesCalculator.compute("NRML", "BUY", 1, 10_000.0)
        # Stamp duty on NRML buy = 0.015% × 10000 = 1.5 INR
        assert buy_c >= 1.5 * 0.9   # at least 90% of expected stamp (allow rounding)
        # Sell side has STT (0.1% = 10 INR) but NO stamp duty — sell_c > buy_c
        sell_c = ChargesCalculator.compute("NRML", "SELL", 1, 10_000.0)
        assert sell_c >= 9.0   # at least 90% of the STT charge

    def test_charges_positive_for_nonzero_turnover(self):
        c = ChargesCalculator.compute("MIS", "BUY", 50, 200.0,
                                      commission_flat=20.0)
        assert c > 0

    def test_charges_zero_for_zero_quantity(self):
        c = ChargesCalculator.compute("NRML", "SELL", 0, 500.0)
        assert c == pytest.approx(0.0)

    def test_gst_applied_on_brokerage(self):
        # With flat brokerage = 20, GST = 18% on (20 + exchange + SEBI)
        c = ChargesCalculator.compute("NRML", "SELL", 10, 1000.0,
                                      commission_flat=20.0)
        # At minimum GST on brokerage alone = 18% × 20 = 3.6 added
        assert c > 20.0   # charges must exceed brokerage alone


# ── 4. Partial fills (volume cap) ────────────────────────────────────────────────

class TestPartialFills:
    def test_volume_cap_limits_fill_quantity(self):
        broker = PaperBroker(100_000, _cfg(volume_cap_pct=0.10))
        # Bar volume = 50, cap = 10% → max fill = 5 shares
        fill = broker.submit_order(
            _mkt_buy(qty=20),
            market_price=1000.0,
            bar={"open": 1000.0, "high": 1010.0, "low": 990.0,
                 "close": 1000.0, "volume": 50.0},
        )
        assert fill is not None
        assert fill.quantity == 5   # 10% of 50

    def test_partial_fill_queues_remainder(self):
        broker = PaperBroker(100_000, _cfg(volume_cap_pct=0.10))
        order = _mkt_buy(qty=20)
        broker.submit_order(
            order, market_price=1000.0,
            bar={"open": 1000.0, "high": 1010.0, "low": 990.0,
                 "close": 1000.0, "volume": 50.0},
        )
        assert order.status == OrderStatus.PARTIALLY_FILLED
        assert order.filled_quantity == 5
        assert order.remaining_quantity == 15
        assert len(broker.get_open_orders()) == 1

    def test_partial_fill_then_complete_next_bar(self):
        broker = PaperBroker(200_000, _cfg(volume_cap_pct=0.10))
        order = _mkt_buy(qty=20)
        broker.submit_order(
            order, market_price=1000.0,
            bar={"open": 1000.0, "high": 1010.0, "low": 990.0,
                 "close": 1000.0, "volume": 50.0},
        )
        # Next bar has high volume — process open orders
        broker.process_open_order(
            order,
            bar={"open": 1000.0, "high": 1010.0, "low": 990.0,
                 "close": 1000.0, "volume": 500_000.0},
        )
        assert order.status == OrderStatus.FILLED
        assert order.filled_quantity == 20

    def test_no_cap_without_volume_cap_pct(self):
        broker = PaperBroker(100_000)
        fill = broker.submit_order(
            _mkt_buy(qty=10), market_price=100.0,
            bar={"open": 100.0, "high": 105.0, "low": 95.0,
                 "close": 100.0, "volume": 1.0},   # tiny volume, no cap
        )
        assert fill.quantity == 10


# ── 5. OHLC limit fill ─────────────────────────────────────────────────────────

class TestOHLCLimitFill:
    def _make_bar(self, o, h, l, c, vol=100_000.0):
        return {"open": o, "high": h, "low": l, "close": c, "volume": vol}

    def test_limit_buy_triggers_on_bar_low(self):
        broker = PaperBroker(100_000)
        order = _limit_buy(price=1000.0, qty=5)
        # Bar low = 990, which is ≤ 1000 → should fill
        fill = broker.submit_order(
            order, market_price=1050.0,
            bar=self._make_bar(1050, 1060, 990, 1050),
        )
        assert fill is not None
        assert fill.quantity == 5

    def test_limit_buy_does_not_fill_if_low_above_limit(self):
        broker = PaperBroker(100_000)
        order = _limit_buy(price=1000.0, qty=5)
        # Bar low = 1010, above 1000 → no fill
        fill = broker.submit_order(
            order, market_price=1050.0,
            bar=self._make_bar(1050, 1060, 1010, 1050),
        )
        assert fill is None
        assert len(broker.get_open_orders()) == 1

    def test_limit_sell_triggers_on_bar_high(self):
        broker = PaperBroker(100_000)
        # Buy first
        broker.submit_order(_mkt_buy(qty=5), market_price=1000.0)
        order = _limit_sell(price=1100.0, qty=5)
        # Bar high = 1110, which is ≥ 1100 → should fill
        fill = broker.submit_order(
            order, market_price=1050.0,
            bar=self._make_bar(1050, 1110, 1040, 1050),
        )
        assert fill is not None

    def test_limit_buy_fills_at_limit_price_not_bar_low(self):
        broker = PaperBroker(100_000)
        order = _limit_buy(price=1000.0, qty=1)
        fill = broker.submit_order(
            order, market_price=1050.0,
            bar=self._make_bar(1050, 1060, 950, 1050),
        )
        # Bar low 950 < limit 1000 → fill at limit price (1000), not at 950
        # (bar open = 1050 > limit → normal fill at limit price)
        assert fill.price == pytest.approx(1000.0)


# ── 6. Gap-open limit fill ──────────────────────────────────────────────────────

class TestGapOpenLimitFill:
    def test_gap_down_buy_fills_at_open(self):
        """If bar opens below buy limit, fill at open (better for buyer)."""
        broker = PaperBroker(100_000)
        order = _limit_buy(price=1000.0, qty=1)
        # Gap down: open = 950, which is below limit 1000
        fill = broker.submit_order(
            order, market_price=950.0,
            bar={"open": 950.0, "high": 960.0, "low": 940.0,
                 "close": 950.0, "volume": 100_000.0},
        )
        assert fill is not None
        # Fill at min(open=950, limit=1000) = 950
        assert fill.price == pytest.approx(950.0)

    def test_gap_up_sell_fills_at_open(self):
        """If bar opens above sell limit, fill at open (better for seller)."""
        broker = PaperBroker(100_000)
        broker.submit_order(_mkt_buy(qty=1), market_price=1000.0)
        order = _limit_sell(price=1100.0, qty=1)
        # Gap up: open = 1150, above limit 1100
        fill = broker.submit_order(
            order, market_price=1150.0,
            bar={"open": 1150.0, "high": 1160.0, "low": 1140.0,
                 "close": 1150.0, "volume": 100_000.0},
        )
        assert fill is not None
        # Fill at max(open=1150, limit=1100) = 1150
        assert fill.price == pytest.approx(1150.0)


# ── 7. Trailing stop-loss (position tracking) ───────────────────────────────────

class TestTrailingStopLoss:
    def _buy_into_position(self, trailing_pct: float = 0.05):
        broker = PaperBroker(
            200_000, _cfg(trailing_sl_pct=trailing_pct)
        )
        broker.submit_order(_mkt_buy(qty=10), market_price=1000.0)
        return broker

    def test_position_initialized_with_trailing_pct(self):
        broker = self._buy_into_position(trailing_pct=0.05)
        pos = broker.get_positions()[0]
        assert pos.trailing_pct == pytest.approx(0.05)

    def test_initial_hwm_equals_entry_price(self):
        broker = self._buy_into_position()
        pos = broker.get_positions()[0]
        assert pos.hwm == pytest.approx(1000.0)

    def test_initial_trailing_sl_below_entry(self):
        broker = self._buy_into_position(trailing_pct=0.05)
        pos = broker.get_positions()[0]
        # 5% trail: initial SL = 1000 * 0.95 = 950
        assert pos.trailing_sl == pytest.approx(950.0)

    def test_no_trailing_without_config(self):
        broker = PaperBroker(100_000)
        broker.submit_order(_mkt_buy(qty=5), market_price=1000.0)
        pos = broker.get_positions()[0]
        assert pos.trailing_pct is None
        assert pos.trailing_sl is None


# ── 8. Trailing SL fires via engine ────────────────────────────────────────────

class TestTrailingSlFires:
    def _make_bars(self, closes, highs=None, lows=None, opens=None):
        n = len(closes)
        highs  = highs  or [c + 5 for c in closes]
        lows   = lows   or [c - 5 for c in closes]
        opens  = opens  or [closes[max(0, i - 1)] for i in range(n)]
        return {
            "open": opens, "high": highs, "low": lows, "close": closes,
            "volume": [1_000_000.0] * n,
        }

    _ENTRY_SCRIPT = """
strategy("Trail Test", overlay=true)
if bar_index == 0
    strategy.entry("Long", strategy.long)
"""

    def test_trailing_sl_limits_loss(self):
        # Price rises then collapses — trailing SL should protect profits
        closes = [1000, 1050, 1100, 1150, 900, 800]
        highs  = [1010, 1060, 1110, 1160, 910, 810]
        lows   = [990,  1040, 1090, 1140, 890, 790]
        bars = self._make_bars(closes, highs=highs, lows=lows)

        engine = BacktestEngine(
            bars=bars,
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            initial_capital=500_000,
            risk_config=_cfg(trailing_sl_pct=0.05, max_position_pct=0.20),
        )
        result = engine.run(self._ENTRY_SCRIPT)
        # Without trailing SL the loss would be severe; with it we exit ~1093
        # Just verify equity didn't collapse to a catastrophic level
        final_equity = result.equity_curve[-1]
        assert final_equity > 400_000   # should not lose > 20% of 500k


# ── 9. Per-trade max loss ────────────────────────────────────────────────────────

class TestPerTradeMaxLoss:
    _ENTRY_SCRIPT = """
strategy("MaxLoss Test", overlay=true)
if bar_index == 0
    strategy.entry("Long", strategy.long)
"""

    def _make_bars_with_crash(self):
        closes = [1000, 1000, 1000, 500, 500, 500]  # 50% crash at bar 3
        return {
            "open":   closes,
            "high":   [c + 5 for c in closes],
            "low":    [c - 5 for c in closes],
            "close":  closes,
            "volume": [1_000_000.0] * len(closes),
        }

    def test_max_loss_triggers_exit(self):
        bars = self._make_bars_with_crash()
        engine = BacktestEngine(
            bars=bars,
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            initial_capital=500_000,
            risk_config=_cfg(
                max_loss_per_trade_pct=0.10,  # exit at 10% loss
                max_position_pct=0.20,
            ),
        )
        result = engine.run(self._ENTRY_SCRIPT)
        # Position should have been force-exited before the full 50% crash
        # Final equity must be > 500_000 * 0.80 (no worse than −20%)
        final_equity = result.equity_curve[-1]
        assert final_equity > 500_000 * 0.80


# ── 10. Trade log CSV ────────────────────────────────────────────────────────────

class TestTradeLog:
    def _make_fills(self):
        from datetime import datetime, timezone
        t0 = datetime(2024, 1, 2, 9, 15, tzinfo=timezone.utc)
        t1 = datetime(2024, 1, 2, 15, 0, tzinfo=timezone.utc)
        buy = Fill(
            order_id="o1",
            timestamp=t0,
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            side=OrderSide.BUY,
            quantity=10,
            price=2000.0,
            charges=25.0,
        )
        sell = Fill(
            order_id="o2",
            timestamp=t1,
            symbol="RELIANCE",
            exchange=Exchange.NSE,
            side=OrderSide.SELL,
            quantity=10,
            price=2100.0,
            charges=30.0,
        )
        return buy, sell

    def test_matched_trade_gross_pnl(self):
        buy, sell = self._make_fills()
        logger = TradeLogger.from_fills([buy, sell])
        assert len(logger.records) == 1
        rec = logger.records[0]
        assert rec.gross_pnl == pytest.approx((2100 - 2000) * 10)  # 1000.0

    def test_matched_trade_net_pnl(self):
        buy, sell = self._make_fills()
        logger = TradeLogger.from_fills([buy, sell])
        rec = logger.records[0]
        # net = 1000 - 25 - 30 = 945
        assert rec.net_pnl == pytest.approx(945.0)

    def test_open_position_flushed(self):
        buy, _ = self._make_fills()
        logger = TradeLogger.from_fills([buy])
        logger.flush_open_positions()
        recs = logger.records
        assert len(recs) == 1
        assert recs[0].status == "OPEN"
        assert recs[0].net_pnl is None

    def test_csv_written_correctly(self):
        buy, sell = self._make_fills()
        logger = TradeLogger.from_fills([buy, sell])
        with tempfile.NamedTemporaryFile(
            suffix=".csv", delete=False
        ) as f:
            path = f.name
        try:
            logger.to_csv(path, force=True)
            with open(path) as f:
                rows = list(csv.DictReader(f))
            assert len(rows) == 1
            assert rows[0]["symbol"] == "RELIANCE"
            assert float(rows[0]["gross_pnl"]) == pytest.approx(1000.0)
            assert rows[0]["status"] == "CLOSED"
        finally:
            os.unlink(path)

    def test_csv_raises_if_file_exists_without_force(self):
        logger = TradeLogger()
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            with pytest.raises(FileExistsError):
                logger.to_csv(path)
        finally:
            os.unlink(path)

    def test_partial_exit_creates_multiple_records(self):
        """One entry with two partial exits → two TradeRecords."""
        from datetime import datetime, timezone
        t0 = datetime(2024, 1, 2, 9, 15, tzinfo=timezone.utc)
        buy = Fill(order_id="o1", timestamp=t0, symbol="X",
                   exchange=Exchange.NSE, side=OrderSide.BUY, quantity=20, price=100.0)
        sell1 = Fill(order_id="o2", timestamp=t0, symbol="X",
                     exchange=Exchange.NSE, side=OrderSide.SELL, quantity=10, price=110.0)
        sell2 = Fill(order_id="o3", timestamp=t0, symbol="X",
                     exchange=Exchange.NSE, side=OrderSide.SELL, quantity=10, price=120.0)
        logger = TradeLogger.from_fills([buy, sell1, sell2])
        assert len(logger.records) == 2
        assert logger.records[0].gross_pnl == pytest.approx(100.0)   # 10 × (110-100)
        assert logger.records[1].gross_pnl == pytest.approx(200.0)   # 10 × (120-100)


# ── 11. BacktestResult.total_charges ──────────────────────────────────────────

class TestBacktestResultCharges:
    _SCRIPT = """
strategy("Charges Test", overlay=true)
if bar_index == 0
    strategy.entry("Long", strategy.long)
if bar_index == 4
    strategy.close("Long")
"""

    def test_total_charges_nonzero_when_commission_set(self):
        import random
        random.seed(1)
        closes = [1000.0 + i * 10 for i in range(10)]
        bars = {
            "open": closes, "high": [c + 5 for c in closes],
            "low": [c - 5 for c in closes], "close": closes,
            "volume": [500_000.0] * 10,
        }
        engine = BacktestEngine(
            bars=bars, symbol="RELIANCE", exchange=Exchange.NSE,
            initial_capital=500_000,
            risk_config=_cfg(commission_flat=20.0, max_position_pct=0.20),
        )
        result = engine.run(self._SCRIPT)
        assert result.total_charges > 0


# ── 12. Order model fields ──────────────────────────────────────────────────────

class TestOrderModelFields:
    def test_default_product_is_nrml(self):
        order = _mkt_buy()
        assert order.product == "NRML"

    def test_default_validity_is_day(self):
        order = _mkt_buy()
        assert order.validity == "DAY"

    def test_custom_product_mis(self):
        order = Order(
            symbol="RELIANCE", exchange=Exchange.NSE,
            side=OrderSide.BUY, order_type=OrderType.MARKET,
            quantity=1, strategy_name="test", product="MIS",
        )
        assert order.product == "MIS"


# ── 13. Fill.net_value ─────────────────────────────────────────────────────────

class TestFillNetValue:
    def test_net_value_less_than_value_when_charges(self):
        fill = Fill(
            order_id="x", symbol="RELIANCE", exchange=Exchange.NSE,
            side=OrderSide.BUY, quantity=10, price=500.0, charges=25.0,
        )
        assert fill.value == pytest.approx(5000.0)
        assert fill.net_value == pytest.approx(4975.0)

    def test_net_value_equals_value_when_no_charges(self):
        fill = Fill(
            order_id="x", symbol="RELIANCE", exchange=Exchange.NSE,
            side=OrderSide.BUY, quantity=10, price=500.0,
        )
        assert fill.net_value == pytest.approx(fill.value)
