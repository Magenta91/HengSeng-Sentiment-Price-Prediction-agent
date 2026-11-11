"""Check that the configured market-data provider really covers every configured ticker.

    python -m scripts.check_market_coverage [--days 400]

Do this BEFORE trusting predictions: coverage of HK/KR tickers is never assumed.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta

from app.config import Settings
from app.core.config_models import ExchangesConfig
from app.integrations.market_data_client import build_provider
from app.utils import utcnow


async def main(days: int) -> None:
    s = Settings()
    provider, ex_cfg = build_provider(s.market_data_provider, s.market_data_csv_dir), ExchangesConfig.load(s.exchanges_config_path)
    end = utcnow().date()
    print(f"provider={provider.name} window={days}d")
    for ex in ex_cfg.exchanges.values():
        cal = ex.calendar()
        for a in ex.assets:
            try:
                bars = await provider.get_daily_bars(a.symbol, end - timedelta(days=days), end, cal)
                bad = sum(1 for b in bars if not (b.low <= b.close <= b.high))
                print(f"  OK   {a.symbol:10} bars={len(bars):4} {bars[0].session_date}..{bars[-1].session_date} "
                      f"adj_close={'yes' if any(b.adj_close for b in bars) else 'no'} close-outside-range={bad}")
            except Exception as exc:  # noqa: BLE001
                print(f"  FAIL {a.symbol:10} {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=400)
    asyncio.run(main(p.parse_args().days))
