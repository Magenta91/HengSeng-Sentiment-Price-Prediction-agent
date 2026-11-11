from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.core.config_models import ExchangesConfig
from app.core.orchestrator import PipelineConfig, PipelineDependencies, PipelineOrchestrator
from app.demo.fixtures import DemoMarketProvider, DemoNewsClient, DemoSocialAdapter
from app.repositories.memory import MemoryRepository
from app.services.model_service import ModelService

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)      # a Tuesday, mid-session in HK


def exchanges() -> ExchangesConfig:
    return ExchangesConfig.load(ROOT / "config" / "exchanges.json")


async def demo_model(directory: str | None = None) -> ModelService:
    from app.demo.artifacts import build_demo_artifacts
    d = directory or tempfile.mkdtemp()
    await build_demo_artifacts(d, ROOT / "config" / "exchanges.json")
    return ModelService.load(Path(d) / "xgboost_model.joblib", Path(d) / "model_metadata.json")


def make_deps(model, **overrides) -> PipelineDependencies:
    base = dict(repo=MemoryRepository(), exchanges=exchanges(), model_service=model,
                social_adapters={
                    "lihkg": DemoSocialAdapter("lihkg"), 
                    "xueqiu": DemoSocialAdapter("xueqiu", with_engagement=False),
                    "naver_stock_discussion": DemoSocialAdapter("naver_stock_discussion", with_engagement=True)
                },
                news_client=DemoNewsClient(), market_provider=DemoMarketProvider(), is_demo=True, clock=lambda: NOW)
    base.update(overrides)
    return PipelineDependencies(**base)


async def no_sleep(_):  # keeps retry tests instant
    return None


def orchestrator(deps, **cfg) -> PipelineOrchestrator:
    return PipelineOrchestrator(deps, PipelineConfig(worker_base_delay=0.0, **cfg), sleep=no_sleep)
