"""Abstract worker lifecycle shared by every pipeline step."""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.core.exceptions import WorkerExecutionError, WorkerValidationError
from app.utils import utcnow


@dataclass
class WorkerResult:
    worker: str
    output: Any
    started_at: Any
    duration_s: float
    metrics: dict = field(default_factory=dict)


class BaseWorker(ABC):
    """Standard lifecycle: validate -> log -> run -> time -> wrap errors.

    Exceptions are never swallowed. Any failure is logged and re-raised as
    :class:`WorkerExecutionError` (original exception kept as ``cause``), so the
    orchestrator always knows whether a worker failed and whether it is retryable.
    Workers receive collaborators (clients, repositories, services) via constructors.
    """

    timeout_s: float | None = None

    def __init__(self, name: str, logger: logging.Logger | None = None):
        self.name = name
        self.logger = logger or logging.getLogger(f"worker.{name}")
        self._metrics: dict = {}

    def metric(self, key: str, value: Any) -> None:
        self._metrics[key] = value

    async def execute(self, payload: Any, context: dict | None = None) -> WorkerResult:
        context = context or {}
        self._metrics = {}
        started_at = utcnow()
        t0 = time.perf_counter()
        extra = {"worker": self.name, "run_id": context.get("run_id")}
        try:
            self.validate(payload)
            self.logger.info("worker_started", extra=extra)
            coro = self.run(payload, context)
            output = await (asyncio.wait_for(coro, self.timeout_s) if self.timeout_s else coro)
        except Exception as exc:
            duration = time.perf_counter() - t0
            self.logger.error("worker_failed", extra={**extra, "error": f"{type(exc).__name__}: {exc}",
                                                      "duration_s": round(duration, 3)})
            raise WorkerExecutionError(self.name, exc, duration) from exc
        duration = time.perf_counter() - t0
        self.logger.info("worker_completed", extra={**extra, "duration_s": round(duration, 3), **self._metrics})
        return WorkerResult(self.name, output, started_at, duration, dict(self._metrics))

    def validate(self, payload: Any) -> None:
        if payload is None:
            raise WorkerValidationError(f"{self.name}: payload cannot be None")

    @abstractmethod
    async def run(self, payload: Any, context: dict) -> Any:
        raise NotImplementedError
