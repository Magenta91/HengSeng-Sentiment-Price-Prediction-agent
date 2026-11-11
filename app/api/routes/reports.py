from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_container, resolve_exchange
from app.bootstrap import Container

router = APIRouter(prefix="/api", tags=["reports"])


@router.get("/reports/latest")
async def latest_report(exchange: str | None = None, mode: str | None = None, c: Container = Depends(get_container)):
    ex = resolve_exchange(c, exchange)
    where = {"exchange_code": ex.code}
    if mode:
        where["mode"] = mode
    runs = await c.repo.select("pipeline_runs", where=where, order_by="started_at", descending=True)
    for run in runs:
        report = (run.get("summary") or {}).get("report")
        if report and run.get("status") in ("succeeded", "partial"):
            return report
    raise HTTPException(404, f"no completed report for {ex.code}; start one with POST /api/pipeline/runs")
