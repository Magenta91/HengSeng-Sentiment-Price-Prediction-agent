"""Market-data provider adapters. Coverage for HK/KR tickers is NOT assumed: run
``scripts/check_market_coverage.py`` against your provider before trusting results."""
from __future__ import annotations

import asyncio
import csv
from datetime import date
from pathlib import Path
from typing import Protocol

from app.core.exceptions import IntegrationError
from app.schemas.records import MarketBar
from app.utils import UTC


class MarketDataProvider(Protocol):
    name: str

    async def get_daily_bars(self, symbol: str, start: date, end: date, calendar) -> list[MarketBar]: ...


def _bar(symbol, d: date, o, h, l, c, v, adj, calendar, provider) -> MarketBar:
    return MarketBar(symbol=symbol, session_date=d, ts=calendar.session_close_utc(d).astimezone(UTC),
                     open=float(o), high=float(h), low=float(l), close=float(c), volume=float(v or 0),
                     adj_close=None if adj in (None, "") else float(adj), provider=provider)


class CsvMarketDataProvider:
    """Reads ``<dir>/<SYMBOL>.csv`` with columns date,open,high,low,close,volume[,adj_close].

    Useful offline, for reproducing Colab datasets, and for providers without an API client.
    """

    name = "csv"

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    async def get_daily_bars(self, symbol, start, end, calendar) -> list[MarketBar]:
        path = self.directory / f"{symbol}.csv"
        if not path.exists():
            raise IntegrationError(f"no CSV for {symbol} in {self.directory}")
        bars = []
        with path.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                d = date.fromisoformat(r["date"][:10])
                if start <= d <= end:
                    bars.append(_bar(symbol, d, r["open"], r["high"], r["low"], r["close"], r.get("volume"),
                                     r.get("adj_close"), calendar, self.name))
        return sorted(bars, key=lambda b: b.session_date)


class YFinanceProvider:
    """yfinance adapter (unofficial Yahoo endpoints). Verify HK ('0700.HK') and KR ('005930.KS')
    coverage and data quality yourself - Yahoo may rate-limit, change, or omit instruments."""

    name = "yfinance"

    async def get_daily_bars(self, symbol, start, end, calendar) -> list[MarketBar]:
        return await asyncio.to_thread(self._fetch, symbol, start, end, calendar)

    def _fetch(self, symbol, start, end, calendar) -> list[MarketBar]:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise IntegrationError("yfinance is not installed (pip install yfinance)") from exc
        from datetime import timedelta
        df = yf.Ticker(symbol).history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                                       interval="1d", auto_adjust=False)
        if df is None or df.empty:
            raise IntegrationError(f"yfinance returned no data for {symbol}")
        bars = []
        for idx, r in df.iterrows():
            if r[["Open", "High", "Low", "Close"]].isna().any():
                continue
            bars.append(_bar(symbol, idx.date(), r["Open"], r["High"], r["Low"], r["Close"], r.get("Volume"),
                             r.get("Adj Close"), calendar, self.name))
        return bars


def build_provider(name: str, csv_dir: str | None = None) -> MarketDataProvider:
    name = (name or "").lower()
    if name == "csv":
        return CsvMarketDataProvider(csv_dir or "data/market")
    if name == "yfinance":
        return YFinanceProvider()
    raise IntegrationError(f"unknown MARKET_DATA_PROVIDER '{name}' (supported: csv, yfinance)")
