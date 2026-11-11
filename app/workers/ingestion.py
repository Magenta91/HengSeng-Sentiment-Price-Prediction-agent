from __future__ import annotations

import asyncio
from datetime import timedelta
from urllib.parse import urlparse

from app.core.exceptions import IntegrationError
from app.core.specialized_workers import BaseIngestionWorker
from app.schemas.records import IngestionRequest, IngestionResult, NewsArticle, stable_id
from app.utils import parse_timestamp, utcnow
from app.workers.processing import canonical_url


class SocialIngestionWorker(BaseIngestionWorker):
    """One instance per configured social source; failure of one never affects the others."""

    def __init__(self, adapter, repo, logger=None):
        super().__init__(f"social:{adapter.source_name}", logger)
        self.adapter, self.repo = adapter, repo

    async def fetch(self, request: IngestionRequest, context: dict) -> IngestionResult:
        return await self.adapter.fetch(request)

    async def persist(self, result: IngestionResult, context: dict) -> None:
        if result.records:
            await self.repo.upsert("social_posts", [p.to_row() for p in result.records])


class NewsIngestionWorker(BaseIngestionWorker):
    def __init__(self, exa_client, repo, *, results_per_asset: int = 10, logger=None):
        super().__init__("news:exa", logger)
        self.exa, self.repo, self.n = exa_client, repo, results_per_asset

    async def _one(self, asset, request: IngestionRequest) -> list[NewsArticle]:
        query = f"{asset.name} stock news"
        results = await self.exa.search_news(query, start=request.window_start, end=request.window_end,
                                             num_results=self.n)
        retrieved = utcnow()
        out = []
        for r in results:
            url = r.get("url")
            if not url:
                continue
            title, body = (r.get("title") or "").strip(), (r.get("text") or "").strip()
            out.append(NewsArticle(
                source="exa", source_post_id=stable_id(canonical_url(url)), url=url, title=title,
                text=f"{title}. {body}".strip(". ") if body else title,
                published_at=parse_timestamp(r.get("publishedDate")), retrieved_at=retrieved,
                summary=r.get("summary"), relevance=r.get("score"), source_domain=urlparse(url).netloc,
                query_symbol=asset.symbol, asset_symbols=[asset.symbol],
                provenance={"exa_id": r.get("id"), "query": query, "retrieved_at": retrieved}, raw=r))
        return out

    async def fetch(self, request: IngestionRequest, context: dict) -> IngestionResult:
        outcomes = await asyncio.gather(*[self._one(a, request) for a in request.assets], return_exceptions=True)
        articles, failed = [], {}
        for asset, res in zip(request.assets, outcomes):
            if isinstance(res, BaseException):
                failed[asset.symbol] = f"{type(res).__name__}: {res}"
            else:
                articles.extend(res)
        if len(failed) == len(request.assets):
            raise next(r for r in outcomes if isinstance(r, BaseException))
        return IngestionResult("exa", "news", articles, {"failed_assets": failed})

    async def persist(self, result: IngestionResult, context: dict) -> None:
        if result.records:
            await self.repo.upsert("news_articles", [a.to_row() for a in result.records])


class MarketDataWorker(BaseIngestionWorker):
    def __init__(self, provider, repo, *, history_days: int = 400, logger=None):
        super().__init__("market_data", logger)
        self.provider, self.repo, self.history_days = provider, repo, history_days

    async def fetch(self, request: IngestionRequest, context: dict) -> IngestionResult:
        cal = request.exchange.calendar()
        end = request.window_end.astimezone(cal.tz).date()
        start = end - timedelta(days=self.history_days)
        outcomes = await asyncio.gather(
            *[self.provider.get_daily_bars(a.symbol, start, end, cal) for a in request.assets], return_exceptions=True)
        bars, failed = [], {}
        for asset, res in zip(request.assets, outcomes):
            if isinstance(res, BaseException):
                failed[asset.symbol] = f"{type(res).__name__}: {res}"
            elif not res:
                failed[asset.symbol] = "provider returned no bars"
            else:
                bars.extend(res)
        if not bars:
            raise IntegrationError(f"no market data for any asset: {failed}")
        return IngestionResult(self.provider.name, "market", bars, {"failed_symbols": failed, "provider": self.provider.name})

    async def persist(self, result: IngestionResult, context: dict) -> None:
        await self.repo.upsert("market_bars", [b.to_row() for b in result.records])
