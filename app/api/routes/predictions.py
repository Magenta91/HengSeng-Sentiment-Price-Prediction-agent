from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_container, resolve_exchange
from app.bootstrap import Container
from app.repositories.base import Op

router = APIRouter(prefix="/api", tags=["predictions"])


@router.get("/predictions")
async def latest_predictions(exchange: str | None = None, c: Container = Depends(get_container)):
    """Latest model prediction per asset (and its evaluation once the actual session is known)."""
    ex = resolve_exchange(c, exchange)
    symbols = [a.symbol for a in ex.assets]
    preds = await c.repo.select("predictions", where={"symbol": Op("in", symbols)}, order_by="cutoff_at")
    evals = {e["prediction_key"]: e for e in await c.repo.select("prediction_evaluations", where={"symbol": Op("in", symbols)})}
    latest = {p["symbol"]: p for p in preds}                    # ascending order: last write wins
    return {"exchange": ex.code, "disclaimer": "Model estimates only; not financial advice.",
            "predictions": [{**p, "evaluation": evals.get(p["prediction_key"])} for p in latest.values()]}
