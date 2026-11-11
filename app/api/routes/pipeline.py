from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_container
from app.bootstrap import Container
from app.core.exceptions import ConfigError
from app.schemas.api import RunCreated, RunRequest, RunStatus, WorkerRunOut

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.post("/runs", response_model=RunCreated, status_code=202)
async def start_run(body: RunRequest, c: Container = Depends(get_container)):
    """Create a run and execute it in the background; poll ``GET /runs/{run_id}`` for progress."""
    orch = c.orchestrator(body.mode)
    try:
        run_id = await orch.start_run(body.exchange, lookback_days=body.lookback_days)
    except ConfigError as exc:
        raise HTTPException(404, str(exc)) from exc
    task = asyncio.create_task(orch.execute_run(run_id))
    c.tasks.add(task)                      # keep a reference so the task is not garbage-collected
    task.add_done_callback(c.tasks.discard)
    return RunCreated(run_id=run_id, status="pending", status_url=f"/api/pipeline/runs/{run_id}")


@router.get("/runs/{run_id}", response_model=RunStatus)
async def get_run(run_id: str, c: Container = Depends(get_container)):
    runs = await c.repo.select("pipeline_runs", where={"run_id": run_id})
    if not runs:
        raise HTTPException(404, f"run '{run_id}' not found")
    run = runs[0]
    workers = await c.repo.select("worker_runs", where={"run_id": run_id}, order_by="started_at")
    summary = run.get("summary") or {}
    return RunStatus(
        run_id=run_id, exchange_code=run.get("exchange_code"), mode=run.get("mode"), status=run.get("status"),
        started_at=run.get("started_at"), finished_at=run.get("finished_at"), error=run.get("error"),
        warnings=summary.get("warnings", []),
        workers=[WorkerRunOut(**{k: w.get(k) for k in WorkerRunOut.model_fields}) for w in workers])
