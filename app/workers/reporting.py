from __future__ import annotations

from app.core.exceptions import WorkerValidationError
from app.core.specialized_workers import BaseReportingWorker
from app.services.report_service import build_report, rank_assets


class RankingWorker(BaseReportingWorker):
    """Ranks assets by social mentions, sentiment score and extracted direction."""

    def __init__(self, logger=None):
        super().__init__("ranking", logger)

    async def run(self, payload: dict, context: dict) -> list[dict]:
        agg = payload.get("agg")
        window = agg.window if agg is not None else None
        return rank_assets(payload["assets"], window, payload.get("extractions", []))


class ReportWorker(BaseReportingWorker):
    def __init__(self, logger=None):
        super().__init__("report", logger)

    def validate(self, payload) -> None:
        super().validate(payload)
        if not isinstance(payload, dict) or "exchange" not in payload or "assets" not in payload:
            raise WorkerValidationError("report payload needs 'exchange' and 'assets'")

    async def run(self, payload: dict, context: dict) -> dict:
        return build_report(payload)
