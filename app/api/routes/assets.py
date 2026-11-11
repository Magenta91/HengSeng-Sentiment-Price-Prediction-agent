from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_container
from app.bootstrap import Container
from app.schemas.api import AssetOut, ExchangeOut, SourceStatus

router = APIRouter(prefix="/api", tags=["assets"])


@router.get("/assets", response_model=list[ExchangeOut])
async def list_assets(c: Container = Depends(get_container)):
    """Configured exchanges, their assets and whether each social source is operational."""
    out = []
    for ex in c.exchanges.exchanges.values():
        out.append(ExchangeOut(
            code=ex.code, name=ex.name, timezone=ex.timezone, currency=ex.currency,
            assets=[AssetOut(symbol=a.symbol, name=a.name, exchange=ex.code, currency=ex.currency) for a in ex.assets],
            sources=[SourceStatus(name=s.name, operational=s.operational_problem() is None, problem=s.operational_problem())
                     for s in ex.sources]))
    return out


@router.get("/assets/{ticker}/intelligence")
async def asset_intelligence(ticker: str, limit: int = 50, c: Container = Depends(get_container)):
    """Latest extracted intelligence (with evidence), daily sentiment aggregates and news for one asset."""
    if not any(a.symbol == ticker for ex in c.exchanges.exchanges.values() for a in ex.assets):
        raise HTTPException(404, f"unknown ticker '{ticker}'")
    ext = await c.repo.select("intelligence_extractions", where={"asset_symbol": ticker}, order_by="source_timestamp",
                              descending=True, limit=min(limit, 200))
    agg = await c.repo.select("sentiment_aggregates", where={"asset_symbol": ticker}, order_by="bucket_date", descending=True, limit=120)
    news = [n for n in await c.repo.select("news_articles", order_by="published_at", descending=True)
            if ticker in (n.get("asset_symbols") or [])][:20]
    return {"ticker": ticker, "extractions": [e["payload"] | {"source": e.get("source"), "source_timestamp": e.get("source_timestamp"),
                                                               "model": e.get("model_name")} for e in ext],
            "aggregates": [{k: a[k] for k in ("bucket_date", "scope", "mention_count", "metrics")} for a in agg],
            "news": [{k: n.get(k) for k in ("title", "url", "published_at", "retrieved_at", "source_domain", "relevance")} for n in news]}
