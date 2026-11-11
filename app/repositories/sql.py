"""SQLAlchemy repository (PostgreSQL in production; SQLite also works for local experiments).

Synchronous SQLAlchemy runs in worker threads via ``asyncio.to_thread`` so the event loop
stays free for concurrent ingestion. Upsert = UPDATE by natural key, INSERT if no row matched,
and a single retry on a concurrent-insert race - portable across dialects.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import and_, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.db.models import TABLES
from app.repositories.base import TABLE_KEYS, Op


class SqlRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    async def upsert(self, table: str, rows: list[dict]) -> int:
        return await asyncio.to_thread(self._upsert, table, rows)

    async def select(self, table: str, *, where=None, order_by=None, descending=False, limit=None) -> list[dict]:
        return await asyncio.to_thread(self._select, table, where, order_by, descending, limit)

    def _upsert(self, table: str, rows: list[dict]) -> int:
        tbl, keys = TABLES[table].__table__, TABLE_KEYS[table]
        with self.engine.begin() as conn:
            for row in rows:
                clean = {k: v for k, v in row.items() if k in tbl.c and k != "id"}
                missing = [k for k in keys if clean.get(k) is None]
                if missing:
                    raise ValueError(f"{table}: row missing key columns {missing}")
                cond = and_(*[tbl.c[k] == clean[k] for k in keys])
                values = {k: v for k, v in clean.items() if k not in keys}
                updated = conn.execute(update(tbl).where(cond).values(**values)).rowcount if values else 0
                if updated == 0:
                    exists = conn.execute(select(tbl.c.id).where(cond)).first() if "id" in tbl.c else None
                    if exists is None:
                        try:
                            with conn.begin_nested():
                                conn.execute(insert(tbl).values(**clean))
                        except IntegrityError:  # concurrent insert won the race
                            if values:
                                conn.execute(update(tbl).where(cond).values(**values))
        return len(rows)

    def _select(self, table, where, order_by, descending, limit) -> list[dict]:
        tbl = TABLES[table].__table__
        stmt = select(tbl)
        for col, cond in (where or {}).items():
            c = tbl.c[col]
            if not isinstance(cond, Op):
                cond = Op("eq", cond)
            expr = {"eq": lambda: c == cond.value, "ne": lambda: c != cond.value, "gt": lambda: c > cond.value,
                    "gte": lambda: c >= cond.value, "lt": lambda: c < cond.value, "lte": lambda: c <= cond.value,
                    "in": lambda: c.in_(list(cond.value)),
                    "isnull": lambda: c.is_(None) if cond.value in (None, True) else c.is_not(None)}[cond.op]()
            stmt = stmt.where(expr)
        if order_by:
            col = tbl.c[order_by]
            stmt = stmt.order_by(col.desc() if descending else col.asc())
        if limit:
            stmt = stmt.limit(limit)
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(stmt)]
