"""Repository contract shared by the in-memory and SQL implementations.

A deliberately small, table-oriented interface (``upsert`` + ``select``) keeps
workers independent from SQLAlchemy and makes ingestion idempotent: rows are keyed
by the natural unique keys below, and ``upsert`` merges only the supplied columns.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

# Natural unique key per table (also used as UNIQUE constraints in db/models.py).
TABLE_KEYS: dict[str, tuple[str, ...]] = {
    "exchanges": ("code",),
    "assets": ("symbol",),
    "pipeline_runs": ("run_id",),
    "worker_runs": ("run_id", "worker_name"),
    "social_posts": ("source", "source_post_id"),
    "news_articles": ("dedup_key", "query_symbol"),
    "market_bars": ("symbol", "interval", "ts", "provider"),
    "intelligence_extractions": ("source_type", "source_id", "asset_symbol", "model_name"),
    "sentiment_aggregates": ("asset_symbol", "bucket_date", "scope"),
    "feature_snapshots": ("run_id", "asset_symbol"),
    "predictions": ("prediction_key",),
    "prediction_evaluations": ("prediction_key",),
}


@dataclass(frozen=True)
class Op:
    """Filter operator: ``Op("gte", value)``. ops: eq ne gt gte lt lte in isnull."""

    op: str
    value: Any = None


class Repository(Protocol):
    async def upsert(self, table: str, rows: list[dict]) -> int: ...

    async def select(self, table: str, *, where: dict | None = None, order_by: str | None = None,
                     descending: bool = False, limit: int | None = None) -> list[dict]: ...


def matches(row: dict, where: dict | None) -> bool:
    for col, cond in (where or {}).items():
        val = row.get(col)
        if not isinstance(cond, Op):
            cond = Op("eq", cond)
        o, c = cond.op, cond.value
        if o == "eq" and val != c: return False
        if o == "ne" and val == c: return False
        if o == "in" and val not in c: return False
        if o == "isnull" and ((val is None) != bool(c if c is not None else True)): return False
        if o in ("gt", "gte", "lt", "lte"):
            if val is None: return False
            if o == "gt" and not val > c: return False
            if o == "gte" and not val >= c: return False
            if o == "lt" and not val < c: return False
            if o == "lte" and not val <= c: return False
    return True
