"""In-memory repository: used by tests and by ``STORAGE_BACKEND=memory`` (no Postgres needed)."""
from __future__ import annotations

import copy
from collections import defaultdict
from itertools import count

from app.repositories.base import TABLE_KEYS, matches


class MemoryRepository:
    def __init__(self):
        self._tables: dict[str, dict[tuple, dict]] = defaultdict(dict)
        self._ids = defaultdict(lambda: count(1))

    async def upsert(self, table: str, rows: list[dict]) -> int:
        keys = TABLE_KEYS[table]
        for row in rows:
            missing = [k for k in keys if k not in row]
            if missing:
                raise ValueError(f"{table}: row missing key columns {missing}")
            key = tuple(row[k] for k in keys)
            existing = self._tables[table].get(key)
            if existing is None:
                existing = {"id": next(self._ids[table])}
                self._tables[table][key] = existing
            existing.update(copy.deepcopy(row))
        return len(rows)

    async def select(self, table: str, *, where: dict | None = None, order_by: str | None = None,
                     descending: bool = False, limit: int | None = None) -> list[dict]:
        rows = [copy.deepcopy(r) for r in self._tables[table].values() if matches(r, where)]
        if order_by:
            rows.sort(key=lambda r: (r.get(order_by) is None, r.get(order_by)), reverse=descending)
        return rows[:limit] if limit else rows
