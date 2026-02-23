"""TDD tests for Indian market hours service.

NSE / BSE trading hours are 09:15 – 15:30 IST, Monday–Friday.
Written BEFORE the implementation.
"""

import pytest
from datetime import datetime
from freezegun import freeze_time

from algotrader.strategy.market_hours import MarketHours, Session


IST = "Asia/Kolkata"


class TestMarketHoursBasic:
    def test_market_is_open_at_noon_tuesday(self):
        # Tuesday 12:00 IST
        with freeze_time("2024-01-09 06:30:00", tz_offset=0):  # 06:30 UTC = 12:00 IST
            mh = MarketHours()
            assert mh.is_open() is True

    def test_market_is_closed_before_open(self):
        # Tuesday 09:00 IST (before 09:15)
        with freeze_time("2024-01-09 03:30:00"):  # 03:30 UTC = 09:00 IST
            mh = MarketHours()
            assert mh.is_open() is False

    def test_market_is_closed_at_915_exactly(self):
        # 09:15 IST is the opening second — open
        with freeze_time("2024-01-09 03:45:00"):  # 03:45 UTC = 09:15 IST
            mh = MarketHours()
            assert mh.is_open() is True

    def test_market_is_closed_after_1530(self):
        # Tuesday 15:31 IST
        with freeze_time("2024-01-09 10:01:00"):  # 10:01 UTC = 15:31 IST
            mh = MarketHours()
            assert mh.is_open() is False

    def test_market_closes_at_1530(self):
        # 15:30 IST — still open
        with freeze_time("2024-01-09 10:00:00"):  # 10:00 UTC = 15:30 IST
            mh = MarketHours()
            assert mh.is_open() is True

    def test_market_is_closed_on_saturday(self):
        with freeze_time("2024-01-13 06:30:00"):  # Saturday noon IST
            mh = MarketHours()
            assert mh.is_open() is False

    def test_market_is_closed_on_sunday(self):
        with freeze_time("2024-01-14 06:30:00"):  # Sunday noon IST
            mh = MarketHours()
            assert mh.is_open() is False

    def test_market_is_open_on_monday(self):
        with freeze_time("2024-01-08 06:30:00"):  # Monday noon IST
            mh = MarketHours()
            assert mh.is_open() is True

    def test_market_is_open_on_friday(self):
        with freeze_time("2024-01-12 06:30:00"):  # Friday noon IST
            mh = MarketHours()
            assert mh.is_open() is True


class TestMarketSession:
    def test_session_returns_pre_market(self):
        with freeze_time("2024-01-09 03:30:00"):  # 09:00 IST
            mh = MarketHours()
            assert mh.current_session() == Session.PRE_MARKET

    def test_session_returns_market(self):
        with freeze_time("2024-01-09 06:30:00"):  # 12:00 IST
            mh = MarketHours()
            assert mh.current_session() == Session.MARKET

    def test_session_returns_post_market(self):
        with freeze_time("2024-01-09 10:15:00"):  # 15:45 IST
            mh = MarketHours()
            assert mh.current_session() == Session.POST_MARKET

    def test_session_returns_closed_on_weekend(self):
        with freeze_time("2024-01-13 06:30:00"):  # Saturday
            mh = MarketHours()
            assert mh.current_session() == Session.CLOSED


class TestHolidays:
    def test_republic_day_is_holiday(self):
        # 26 January is Republic Day (NSE holiday)
        with freeze_time("2024-01-26 06:30:00"):  # Friday noon IST
            mh = MarketHours()
            assert mh.is_open() is False

    def test_custom_holiday(self):
        mh = MarketHours(extra_holidays=["2024-02-15"])
        with freeze_time("2024-02-15 06:30:00"):
            assert mh.is_open() is False


class TestMarketHoursExplicitDatetime:
    def test_is_open_with_explicit_ist_datetime(self):
        mh = MarketHours()
        # 2024-01-09 (Tuesday) 10:00 IST — open
        dt = datetime(2024, 1, 9, 10, 0, 0)
        assert mh.is_open_at(dt, tz="Asia/Kolkata") is True

    def test_is_closed_with_explicit_ist_datetime_weekend(self):
        mh = MarketHours()
        dt = datetime(2024, 1, 13, 10, 0, 0)  # Saturday
        assert mh.is_open_at(dt, tz="Asia/Kolkata") is False

    def test_is_open_with_utc_datetime(self):
        mh = MarketHours()
        dt = datetime(2024, 1, 9, 6, 0, 0)   # 06:00 UTC = 11:30 IST
        assert mh.is_open_at(dt, tz="UTC") is True
