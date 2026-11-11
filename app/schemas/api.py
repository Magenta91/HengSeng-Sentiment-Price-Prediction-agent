from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    exchange: str = Field(examples=["HKEX"])
    lookback_days: int | None = Field(None, ge=1, le=60)
    mode: Literal["live", "demo"] = "live"


class RunCreated(BaseModel):
    run_id: str
    status: str
    status_url: str


class WorkerRunOut(BaseModel):
    worker_name: str
    status: str | None
    attempts: int | None = None
    duration_s: float | None = None
    error: str | None = None
    details: dict[str, Any] | None = None


class RunStatus(BaseModel):
    run_id: str
    exchange_code: str | None
    mode: str | None
    status: str | None
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None = None
    warnings: list[str] = []
    workers: list[WorkerRunOut] = []


class SourceStatus(BaseModel):
    name: str
    operational: bool
    problem: str | None = None


class AssetOut(BaseModel):
    symbol: str
    name: str
    exchange: str
    currency: str


class ExchangeOut(BaseModel):
    code: str
    name: str
    timezone: str
    currency: str
    assets: list[AssetOut]
    sources: list[SourceStatus]
