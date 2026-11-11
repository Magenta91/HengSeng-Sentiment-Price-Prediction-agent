"""Pipeline orchestrator: owns sequencing, concurrency, retries, dependencies and run state.

Stage graph (workers never call each other):

    ingestion (social sources || news || market, concurrent, isolated failures)
      -> processing (clean -> timestamps -> dedup -> ticker mapping -> engagement)
      -> intelligence (FinBERT? || LLM extraction?) -> signal aggregation
      -> features -> prediction (needs model) -> evaluation / backtest
      -> ranking -> report
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.core.config_models import ExchangeConfig, ExchangesConfig
from app.core.exceptions import WorkerExecutionError
from app.core.retry import RetryPolicy, retry_async
from app.repositories.base import Op
from app.schemas.records import IngestionRequest
from app.services.ticker_service import TickerService
from app.utils import to_json_safe, utcnow
from app.workers.ingestion import MarketDataWorker, NewsIngestionWorker, SocialIngestionWorker
from app.workers.intelligence import AggregationInput, FinancialExtractionWorker, SentimentWorker, SignalAggregationWorker
from app.workers.prediction import (
    BacktestingWorker,
    FeatureEngineeringWorker,
    FeaturePayload,
    PredictionEvaluationWorker,
    PredictionWorker,
)
from app.workers.processing import (
    CleaningWorker,
    DeduplicationWorker,
    EngagementNormalizationWorker,
    TickerMappingWorker,
    TimestampNormalizationWorker,
)
from app.workers.reporting import RankingWorker, ReportWorker

log = logging.getLogger("orchestrator")


@dataclass
class PipelineConfig:
    lookback_days: int = 10
    market_history_days: int = 400
    worker_max_attempts: int = 2          # 1 try + 1 retry for transient worker failures
    worker_base_delay: float = 2.0
    ingestion_timeout_s: float = 900.0
    news_results_per_asset: int = 10
    extraction_concurrency: int = 4
    extraction_max_items: int = 300
    max_items_per_source: int = 500
    run_backtest: bool = True


@dataclass
class PipelineDependencies:
    repo: Any
    exchanges: ExchangesConfig
    model_service: Any
    social_adapters: dict = field(default_factory=dict)     # source name -> adapter
    skipped_sources: dict = field(default_factory=dict)     # source name -> reason (not operational)
    news_client: Any = None
    market_provider: Any = None
    extraction_service: Any = None
    sentiment_scorer: Any = None
    is_demo: bool = False
    clock: Callable[[], datetime] = utcnow


@dataclass
class StageOutcome:
    name: str
    status: str                            # succeeded | failed | skipped
    output: Any = None
    attempts: int = 0
    duration_s: float = 0.0
    error: str | None = None
    reason: str | None = None
    metrics: dict = field(default_factory=dict)
    gap: bool = False                      # a skipped/failed stage that reduces data coverage

    def to_dict(self) -> dict:
        return to_json_safe({"name": self.name, "status": self.status, "attempts": self.attempts,
                             "duration_s": round(self.duration_s, 3), "error": self.error,
                             "reason": self.reason, "metrics": self.metrics})


@dataclass
class RunSummary:
    run_id: str
    status: str
    exchange_code: str
    started_at: datetime
    finished_at: datetime
    outcomes: list[StageOutcome]
    warnings: list[str]
    report: dict | None = None


class PipelineOrchestrator:
    def __init__(self, deps: PipelineDependencies, config: PipelineConfig | None = None, *,
                 sleep: Callable = asyncio.sleep):
        self.deps, self.config, self.sleep = deps, config or PipelineConfig(), sleep
        self.repo = deps.repo
        self.retry = RetryPolicy(max_attempts=self.config.worker_max_attempts, base_delay=self.config.worker_base_delay)

    # ------------------------------------------------------------------ run lifecycle
    async def start_run(self, exchange_code: str, *, lookback_days: int | None = None) -> str:
        ex = self.deps.exchanges.get(exchange_code)          # raises ConfigError for unknown exchange
        now = self.deps.clock()
        run_id = f"run_{now:%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:6]}"
        cfg = {"lookback_days": lookback_days or self.config.lookback_days,
               "market_history_days": self.config.market_history_days}
        await self.repo.upsert("exchanges", [{"code": ex.code, "name": ex.name, "timezone": ex.timezone,
                                              "currency": ex.currency, "close_time": ex.close_time.strftime("%H:%M"),
                                              "holidays": [d.isoformat() for d in sorted(ex.holidays)]}])
        await self.repo.upsert("assets", [{"symbol": a.symbol, "name": a.name, "exchange_code": ex.code,
                                           "currency": ex.currency} for a in ex.assets])
        await self.repo.upsert("pipeline_runs", [{"run_id": run_id, "exchange_code": ex.code, "status": "pending",
                                                  "mode": "demo" if self.deps.is_demo else "live", "config": cfg,
                                                  "started_at": now}])
        return run_id

    async def run(self, exchange_code: str, *, lookback_days: int | None = None) -> RunSummary:
        return await self.execute_run(await self.start_run(exchange_code, lookback_days=lookback_days))

    async def execute_run(self, run_id: str) -> RunSummary:
        run = (await self.repo.select("pipeline_runs", where={"run_id": run_id}))[0]
        ex = self.deps.exchanges.get(run["exchange_code"])
        started = run["started_at"]
        outcomes: list[StageOutcome] = []
        await self.repo.upsert("pipeline_runs", [{"run_id": run_id, "status": "running"}])
        try:
            warnings, report = await self._pipeline(run_id, ex, run, outcomes)
        except Exception as exc:
            log.exception("pipeline_crashed", extra={"run_id": run_id})
            await self.repo.upsert("pipeline_runs", [{"run_id": run_id, "status": "failed", "finished_at": utcnow(),
                                                      "error": f"{type(exc).__name__}: {exc}"}])
            return RunSummary(run_id, "failed", ex.code, started, utcnow(), outcomes, [f"crashed: {exc}"])
        status = self._final_status(outcomes)
        finished = self.deps.clock()
        summary = {"workers": [o.to_dict() for o in outcomes], "warnings": warnings, "report": report}
        await self.repo.upsert("pipeline_runs", [{"run_id": run_id, "status": status, "finished_at": finished,
                                                  "summary": to_json_safe(summary)}])
        return RunSummary(run_id, status, ex.code, started, finished, outcomes, warnings, report)

    @staticmethod
    def _final_status(outcomes: list[StageOutcome]) -> str:
        ingest = [o for o in outcomes if o.name.startswith(("social:", "news:", "market_data"))]
        if ingest and not any(o.status == "succeeded" for o in ingest):
            return "failed"
        if any(o.status == "failed" or (o.status == "skipped" and o.gap) for o in outcomes):
            return "partial"
        return "succeeded"

    # ------------------------------------------------------------------ stage helpers
    async def _record(self, run_id: str, o: StageOutcome, started_at: datetime) -> None:
        await self.repo.upsert("worker_runs", [{
            "run_id": run_id, "worker_name": o.name, "status": o.status, "started_at": started_at,
            "duration_s": o.duration_s, "attempts": o.attempts, "error": o.error,
            "details": to_json_safe({"reason": o.reason, **o.metrics})}])

    async def _stage(self, run_id: str, outcomes: list, worker, payload) -> StageOutcome:
        attempts, started_at = 0, utcnow()

        async def attempt():
            nonlocal attempts
            attempts += 1
            return await worker.execute(payload, {"run_id": run_id})

        try:
            res = await retry_async(attempt, self.retry, sleep=self.sleep,
                                    is_retryable=lambda e: isinstance(e, WorkerExecutionError) and e.transient)
            o = StageOutcome(worker.name, "succeeded", res.output, attempts, res.duration_s, metrics=res.metrics)
        except WorkerExecutionError as exc:
            o = StageOutcome(worker.name, "failed", None, attempts, exc.duration_s, error=str(exc), gap=True)
        outcomes.append(o)
        await self._record(run_id, o, started_at)
        return o

    async def _skip(self, run_id: str, outcomes: list, name: str, reason: str, gap: bool = False) -> StageOutcome:
        o = StageOutcome(name, "skipped", reason=reason, gap=gap)
        outcomes.append(o)
        await self._record(run_id, o, utcnow())
        return o

    # ------------------------------------------------------------------ the pipeline
    async def _pipeline(self, run_id: str, ex: ExchangeConfig, run: dict, outcomes: list) -> tuple[list, dict | None]:
        d, cfg, repo = self.deps, self.config, self.repo
        now = d.clock()
        lookback = (run.get("config") or {}).get("lookback_days") or cfg.lookback_days
        w_start, w_end, cal = now - timedelta(days=lookback), now, ex.calendar()
        assets = list(ex.assets)
        warnings = list(ex.scope_warnings())
        request = IngestionRequest(ex, assets, w_start, w_end, cfg.max_items_per_source)

        # 1) ingestion: independent sources run concurrently; each failure is isolated -------------
        tasks = []
        for src in ex.sources:
            adapter = d.social_adapters.get(src.name)
            if adapter is None:
                reason = d.skipped_sources.get(src.name) or src.operational_problem() or "no adapter configured"
                await self._skip(run_id, outcomes, f"social:{src.name}", reason, gap=True)
                continue
            w = SocialIngestionWorker(adapter, repo)
            w.timeout_s = cfg.ingestion_timeout_s
            tasks.append(self._stage(run_id, outcomes, w, request))
        if d.news_client is not None:
            w = NewsIngestionWorker(d.news_client, repo, results_per_asset=cfg.news_results_per_asset)
            w.timeout_s = cfg.ingestion_timeout_s
            tasks.append(self._stage(run_id, outcomes, w, request))
        else:
            await self._skip(run_id, outcomes, "news:exa", "EXA_API_KEY not configured", gap=True)
        if d.market_provider is not None:
            w = MarketDataWorker(d.market_provider, repo, history_days=cfg.market_history_days)
            w.timeout_s = cfg.ingestion_timeout_s
            tasks.append(self._stage(run_id, outcomes, w, request))
        else:
            await self._skip(run_id, outcomes, "market_data", "no market-data provider configured", gap=True)
        ingest = await asyncio.gather(*tasks)

        posts, articles, bars_by_symbol, ok_social = [], [], {}, 0
        for o in ingest:
            if o.status != "succeeded":
                continue
            r = o.output
            if r.kind == "social":
                posts += r.records
                ok_social += 1
            elif r.kind == "news":
                articles += r.records
                for sym, err in (r.meta.get("failed_assets") or {}).items():
                    warnings.append(f"news retrieval failed for {sym}: {err}")
            elif r.kind == "market":
                for b in r.records:
                    bars_by_symbol.setdefault(b.symbol, []).append(b)
                for sym, err in (r.meta.get("failed_symbols") or {}).items():
                    warnings.append(f"market data unavailable for {sym}: {err}")
        if ok_social < 2:
            warnings.append(f"only {ok_social} social source(s) succeeded; the assignment requires at least two per exchange")

        # 2) processing: strictly ordered transformations ------------------------------------------
        records, proc_ok = posts + articles, True
        chain = [CleaningWorker(), TimestampNormalizationWorker(w_start, w_end), DeduplicationWorker(),
                 TickerMappingWorker(TickerService(assets)), EngagementNormalizationWorker()]
        if not records:
            await self._skip(run_id, outcomes, "processing", "no records were ingested")
            proc_ok = False
        else:
            for w in chain:
                o = await self._stage(run_id, outcomes, w, records)
                if o.status != "succeeded":
                    proc_ok = False
                    break
                records = o.output
            if proc_ok:
                await self._persist_processed(records)

        # 3) intelligence ----------------------------------------------------------------------------
        scores, extractions, agg = [], [], None
        if proc_ok and records:
            if d.sentiment_scorer is not None:
                o = await self._stage(run_id, outcomes, SentimentWorker(d.sentiment_scorer), records)
                scores = o.output if o.status == "succeeded" else []
            else:
                await self._skip(run_id, outcomes, "sentiment:finbert", "FinBERT scorer not enabled")
            if d.extraction_service is not None:
                w = FinancialExtractionWorker(d.extraction_service, repo, {a.symbol: a for a in assets},
                                              concurrency=cfg.extraction_concurrency, max_items=cfg.extraction_max_items)
                o = await self._stage(run_id, outcomes, w, records)
                extractions = o.output.extractions if o.status == "succeeded" else []
            else:
                await self._skip(run_id, outcomes, "financial_extraction", "GEMINI_API_KEY not configured")
            if not scores and not extractions:
                warnings.append("no sentiment source available (no LLM extraction, no FinBERT): sentiment is missing, not neutral")
            o = await self._stage(run_id, outcomes, SignalAggregationWorker(repo),
                                  AggregationInput(records, scores, extractions, cal))
            agg = o.output if o.status == "succeeded" else None
        else:
            await self._skip(run_id, outcomes, "intelligence", "no processed records")

        # 4) features -> prediction -> evaluation ---------------------------------------------------
        feature_rows, predictions, pred_skipped, evaluation, backtest = [], [], {}, None, None
        model = d.model_service
        if bars_by_symbol:
            o = await self._stage(run_id, outcomes, FeatureEngineeringWorker(repo),
                                  FeaturePayload(bars_by_symbol, agg.rows if agg else None, cal, now))
            if o.status == "succeeded":
                feature_rows = o.output.rows
                pred_skipped.update(o.output.skipped)
            if feature_rows and model.available:
                o = await self._stage(run_id, outcomes, PredictionWorker(model, repo, cal), feature_rows)
                if o.status == "succeeded":
                    predictions = o.output.predictions
                    pred_skipped.update(o.output.skipped)
            elif not model.available:
                await self._skip(run_id, outcomes, "prediction", f"model unavailable: {model.unavailable_reason}")
                warnings.append(f"no predictions: {model.unavailable_reason}")
            o = await self._stage(run_id, outcomes, PredictionEvaluationWorker(repo), bars_by_symbol)
            evaluation = o.output["summary"] if o.status == "succeeded" else None
            if cfg.run_backtest and model.available:
                o = await self._stage(run_id, outcomes, BacktestingWorker(model), bars_by_symbol)
                backtest = o.output if o.status == "succeeded" else None
        else:
            await self._skip(run_id, outcomes, "feature_engineering", "no market data", gap=True)
            warnings.append("no market data: no features, predictions or evaluation")

        # 5) reporting -------------------------------------------------------------------------------
        ctx = {"exchange": ex, "assets": assets, "agg": agg, "extractions": extractions,
               "articles": [r for r in records if r.source_type == "news"] if proc_ok else [],
               "bars_by_symbol": bars_by_symbol, "feature_rows": feature_rows, "predictions": predictions,
               "prediction_skipped": pred_skipped, "prediction_unavailable_reason": model.unavailable_reason,
               "evaluation": evaluation, "backtest": backtest, "model_info": model.info(), "is_demo": d.is_demo,
               "history": await self._history(run_id, [a.symbol for a in assets]), "warnings": warnings}
        o = await self._stage(run_id, outcomes, RankingWorker(), ctx)
        ctx["rankings"] = o.output if o.status == "succeeded" else []
        ctx["run"] = {"run_id": run_id, "mode": "demo" if d.is_demo else "live", "exchange_code": ex.code,
                      "window_start": w_start, "window_end": w_end, "lookback_days": lookback}
        ctx["outcomes"] = [x.to_dict() for x in outcomes]
        o = await self._stage(run_id, outcomes, ReportWorker(), ctx)
        report = o.output if o.status == "succeeded" else None
        if report is not None:
            report["workers"] = [x.to_dict() for x in outcomes]
        return warnings, report

    async def _persist_processed(self, records: list) -> None:
        social = [r.to_row() for r in records if r.source_type == "social"]
        news = [r.to_row() for r in records if r.source_type == "news"]
        if social:
            await self.repo.upsert("social_posts", social)
        if news:
            await self.repo.upsert("news_articles", news)

    async def _history(self, run_id: str, symbols: list[str]) -> dict:
        preds = await self.repo.select("predictions", where={"symbol": Op("in", symbols)}, order_by="as_of_date")
        evals = {e["prediction_key"]: e for e in await self.repo.select("prediction_evaluations", where={"symbol": Op("in", symbols)})}
        out: dict[str, list] = {s: [] for s in symbols}
        for p in preds:
            e = evals.get(p["prediction_key"])
            out[p["symbol"]].append({
                "as_of_date": p["as_of_date"], "target_session_date": p["target_session_date"],
                "predicted_return": p["predicted_return"], "predicted_price": p["predicted_price"],
                "actual_price": e["actual_price"] if e else None, "actual_return": e["actual_return"] if e else None,
                "model_version": p["model_version"]})
        return {s: v[-30:] for s, v in out.items()}
