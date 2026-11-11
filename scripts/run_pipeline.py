"""CLI entry point:  python -m scripts.run_pipeline --exchange HKEX [--demo] [--lookback-days 10] [--output report.json]"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.bootstrap import build_container
from app.config import Settings
from app.logging_config import configure_logging


async def main(args) -> int:
    settings = Settings()
    configure_logging(settings.log_level, settings.secrets)
    container = build_container(settings)
    try:
        summary = await container.orchestrator("demo" if args.demo else "live").run(args.exchange, lookback_days=args.lookback_days)
    finally:
        await container.close()
    print(f"\nrun {summary.run_id}: {summary.status.upper()}{'  (DEMO: synthetic data)' if args.demo else ''}")
    for o in summary.outcomes:
        note = o.error or o.reason or ""
        print(f"  {o.name:28} {o.status:10} attempts={o.attempts} {note}")
    for w in summary.warnings:
        print(f"  warning: {w}")
    if summary.report and args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(summary.report, fh, indent=2, ensure_ascii=False)
        print(f"report written to {args.output}")
    return 0 if summary.status != "failed" else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--exchange", required=True)
    p.add_argument("--lookback-days", type=int, default=None)
    p.add_argument("--demo", action="store_true", help="use synthetic demo sources (no credentials needed)")
    p.add_argument("--output")
    sys.exit(asyncio.run(main(p.parse_args())))
