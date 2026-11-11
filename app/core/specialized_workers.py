"""Category base classes. Thin on purpose: they fix the template, not the business logic."""
from __future__ import annotations

from abc import abstractmethod
from typing import Any

from app.core.base_worker import BaseWorker
from app.core.exceptions import WorkerValidationError
from app.schemas.records import IngestionRequest


class BaseIngestionWorker(BaseWorker):
    """fetch from an adapter -> persist raw records with provenance -> return result."""

    def validate(self, payload: Any) -> None:
        super().validate(payload)
        if not isinstance(payload, IngestionRequest):
            raise WorkerValidationError(f"{self.name}: expected IngestionRequest")
        if payload.window_start >= payload.window_end:
            raise WorkerValidationError(f"{self.name}: window_start must precede window_end")
        if not payload.assets:
            raise WorkerValidationError(f"{self.name}: no assets configured")

    async def run(self, payload: IngestionRequest, context: dict):
        result = await self.fetch(payload, context)
        await self.persist(result, context)
        self.metric("records", len(result.records))
        return result

    @abstractmethod
    async def fetch(self, request: IngestionRequest, context: dict): ...

    @abstractmethod
    async def persist(self, result, context: dict) -> None: ...


class BaseProcessingWorker(BaseWorker):
    """Pure transform + validate over a list of records."""

    def validate(self, payload: Any) -> None:
        super().validate(payload)
        if not isinstance(payload, list):
            raise WorkerValidationError(f"{self.name}: expected a list of records")


class BaseIntelligenceWorker(BaseWorker):
    """Sentiment / structured extraction / aggregation over processed records."""

    def validate(self, payload: Any) -> None:
        super().validate(payload)


class BasePredictionWorker(BaseWorker):
    """Feature, inference and evaluation workers; shares a small feature-frame guard."""

    @staticmethod
    def require_columns(df, columns) -> None:
        missing = [c for c in columns if c not in df.columns]
        if missing:
            raise WorkerValidationError(f"missing required columns: {missing}")


class BaseReportingWorker(BaseWorker):
    """Turn stored outputs into report-ready structures."""
