"""Composition root: builds clients, repositories, services and orchestrators from settings."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.core.config_models import ExchangesConfig
from app.core.orchestrator import PipelineConfig, PipelineDependencies, PipelineOrchestrator
from app.demo.fixtures import DemoLLMClient, DemoMarketProvider, DemoNewsClient, DemoSocialAdapter
from app.integrations.apify_client import ApifyClient
from app.integrations.exa_client import ExaClient
from app.integrations.http import HttpxTransport
from app.integrations.llm_client import GeminiLLMClient
from app.integrations.market_data_client import build_provider
from app.integrations.social_adapters import ApifySocialAdapter
from app.repositories.memory import MemoryRepository
from app.services.extraction_service import ExtractionService
from app.services.model_service import ModelService

log = logging.getLogger("bootstrap")


@dataclass
class Container:
    settings: Settings
    repo: object
    exchanges: ExchangesConfig
    live: PipelineOrchestrator
    demo: PipelineOrchestrator
    model_service: ModelService
    transport: HttpxTransport | None = None
    tasks: set = field(default_factory=set)

    def orchestrator(self, mode: str) -> PipelineOrchestrator:
        return self.demo if mode == "demo" else self.live

    async def close(self) -> None:
        if self.transport:
            await self.transport.aclose()


def build_repository(settings: Settings):
    if settings.storage_backend == "postgres":
        from app.db.session import init_db, make_engine
        from app.repositories.sql import SqlRepository
        engine = make_engine(settings.database_url)
        init_db(engine)
        return SqlRepository(engine)
    return MemoryRepository()


def build_live_dependencies(s: Settings, repo, exchanges, model, transport) -> PipelineDependencies:
    adapters, skipped = {}, {}
    client = ApifyClient(s.apify_api_token, transport) if s.apify_api_token else None
    for ex in exchanges.exchanges.values():
        for src in ex.sources:
            problem = src.operational_problem() or (None if client else "APIFY_API_TOKEN not set")
            if problem:
                skipped[src.name] = problem
            else:
                # Use specialized adapters for specific sources
                if src.name == "lihkg":
                    from app.integrations.social_adapters import LIHKGAdapter
                    adapters[src.name] = LIHKGAdapter(src, client)
                elif src.name == "xueqiu":
                    from app.integrations.social_adapters import XueqiuAdapter
                    adapters[src.name] = XueqiuAdapter(src, client)
                elif src.name == "naver_stock_discussion":
                    from app.integrations.social_adapters import NaverStockDiscussionAdapter
                    adapters[src.name] = NaverStockDiscussionAdapter(src, client)
                else:
                    adapters[src.name] = ApifySocialAdapter(src, client)
    return PipelineDependencies(
        repo=repo, exchanges=exchanges, model_service=model, social_adapters=adapters, skipped_sources=skipped,
        news_client=ExaClient(s.exa_api_key, transport) if s.exa_api_key else None,
        market_provider=build_provider(s.market_data_provider, s.market_data_csv_dir),
        extraction_service=ExtractionService(GeminiLLMClient(s.gemini_api_key, s.gemini_model)) if s.gemini_api_key else None,
        sentiment_scorer=_finbert() if s.finbert_enabled else None)


def _finbert():
    from app.services.sentiment_service import FinBertScorer
    return FinBertScorer()


def build_demo_dependencies(repo, exchanges, model) -> PipelineDependencies:
    names = [src.name for ex in exchanges.exchanges.values() for src in ex.sources]
    adapters = {n: DemoSocialAdapter(n, with_engagement=(i % 2 == 0)) for i, n in enumerate(names)}
    return PipelineDependencies(repo=repo, exchanges=exchanges, model_service=model, social_adapters=adapters,
                                news_client=DemoNewsClient(), market_provider=DemoMarketProvider(),
                                extraction_service=ExtractionService(DemoLLMClient()), is_demo=True)


def build_container(settings: Settings) -> Container:
    exchanges = ExchangesConfig.load(settings.exchanges_config_path)
    repo, transport = build_repository(settings), HttpxTransport()
    model = ModelService.load(settings.model_path, settings.model_metadata_path)
    demo_dir = Path(settings.demo_model_dir)
    demo_model = ModelService.load(demo_dir / "xgboost_model.joblib", demo_dir / "model_metadata.json")
    if not model.available:
        log.warning("model_unavailable", extra={"reason": model.unavailable_reason})
    cfg = PipelineConfig(lookback_days=settings.default_lookback_days, market_history_days=settings.market_history_days)
    live = PipelineOrchestrator(build_live_dependencies(settings, repo, exchanges, model, transport), cfg)
    demo = PipelineOrchestrator(build_demo_dependencies(repo, exchanges, demo_model if demo_model.available else model), cfg)
    return Container(settings, repo, exchanges, live, demo, model, transport)
