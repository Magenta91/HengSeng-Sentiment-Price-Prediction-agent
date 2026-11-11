from datetime import UTC, date, datetime, time

from app.ml.trading_calendar import TradingCalendar
from app.utils import parse_timestamp, to_json_safe

HK = TradingCalendar("HKEX", "Asia/Hong_Kong", time(16, 10), frozenset({date(2026, 11, 12)}))


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def test_parse_timestamp_variants_normalise_to_utc():
    assert parse_timestamp("2026-11-10T08:00:00Z") == utc(2026, 11, 10, 8)
    assert parse_timestamp(1_762_761_600) == utc(2025, 11, 10, 8)
    assert parse_timestamp(1_762_761_600_000) == utc(2025, 11, 10, 8)      # milliseconds
    from zoneinfo import ZoneInfo
    assert parse_timestamp("2026.11.10 14:32", ZoneInfo("Asia/Seoul")) == utc(2026, 11, 10, 5, 32)
    assert parse_timestamp("garbage") is None and parse_timestamp(None) is None


def test_to_json_safe_removes_nan():
    import math
    assert to_json_safe({"a": math.nan, "b": [1.5, float("inf")]}) == {"a": None, "b": [1.5, None]}


def test_bucket_date_after_close_rolls_to_next_session():
    before = utc(2026, 11, 10, 7, 0)       # 15:00 HKT Tuesday -> same session
    after = utc(2026, 11, 10, 9, 0)        # 17:00 HKT Tuesday -> after close -> Wednesday
    assert HK.bucket_date(before) == date(2026, 11, 10)
    assert HK.bucket_date(after) == date(2026, 11, 11)


def test_weekend_and_holiday_roll_forward():
    assert HK.bucket_date(utc(2026, 11, 14, 3)) == date(2026, 11, 16)       # Saturday -> Monday
    assert HK.next_trading_day(date(2026, 11, 11)) == date(2026, 11, 13)    # Thursday 12th is a configured holiday


def test_last_complete_session_excludes_open_session():
    assert HK.last_complete_session(utc(2026, 11, 10, 4)) == date(2026, 11, 9)    # 12:00 HKT, still open
    assert HK.last_complete_session(utc(2026, 11, 10, 9)) == date(2026, 11, 10)   # after close
