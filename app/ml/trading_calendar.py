"""Exchange session calendar used identically by training (Colab) and inference.

Deliberately dependency-free (stdlib only). Weekends are always closed; exchange
holidays come from configuration. Holiday lists are NOT bundled because they must
be maintained per year - see README "Limitations". For daily bars the *next
session* used as the prediction target is always the next bar the provider
returns, so a missing holiday entry cannot corrupt training labels.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class TradingCalendar:
    exchange_code: str
    timezone_name: str
    close_time: time
    holidays: frozenset = frozenset()

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def next_trading_day(self, d: date) -> date:
        """First trading day strictly after ``d``."""
        d = d + timedelta(days=1)
        while not self.is_trading_day(d):
            d += timedelta(days=1)
        return d

    def previous_trading_day(self, d: date) -> date:
        """Last trading day strictly before ``d``."""
        d = d - timedelta(days=1)
        while not self.is_trading_day(d):
            d -= timedelta(days=1)
        return d

    def session_close_utc(self, d: date) -> datetime:
        return datetime.combine(d, self.close_time, tzinfo=self.tz).astimezone(UTC)

    def bucket_date(self, ts: datetime) -> date:
        """Trading session whose feature window contains ``ts``.

        Window for session D is (close of previous session, close of D]. Anything
        after D's close (or on a non-trading day) belongs to the NEXT session, so
        features for D never contain information published after D's close.
        """
        if ts.tzinfo is None:
            raise ValueError("bucket_date requires a timezone-aware timestamp")
        local = ts.astimezone(self.tz)
        d = local.date()
        if self.is_trading_day(d) and local.time() <= self.close_time:
            return d
        return self.next_trading_day(d)

    def last_complete_session(self, now: datetime) -> date:
        """Most recent session whose close is at or before ``now``."""
        local = now.astimezone(self.tz)
        d = local.date()
        if self.is_trading_day(d) and local.time() >= self.close_time:
            return d
        return self.previous_trading_day(d)
