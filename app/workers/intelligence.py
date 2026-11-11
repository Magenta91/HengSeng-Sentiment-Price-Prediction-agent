from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from app.core.exceptions import IntegrationError, WorkerValidationError
from app.core.specialized_workers import BaseIntelligenceWorker
from app.ml.feature_schema import sentiment_label, summarize_sentiment
from app.schemas.records import Extraction, SentimentScore
from app.utils import UTC, to_json_safe


class SentimentWorker(BaseIntelligenceWorker):
    """Optional pretrained-sentiment (FinBERT) score for every (record, symbol)."""

    def __init__(self, scorer, logger=None):
        super().__init__("sentiment:finbert", logger)
        self.scorer = scorer

    async def run(self, payload: list, context: dict) -> list[SentimentScore]:
        texts = list(dict.fromkeys(r.text for r in payload))
        scores = dict(zip(texts, await self.scorer.score(texts))) if texts else {}
        out = [SentimentScore(r.record_id, r.source_type, sym, scores[r.text], sentiment_label(scores[r.text]),
                              self.scorer.model_name) for r in payload for sym in r.asset_symbols]
        self.metric("scored", len(out))
        return out


@dataclass
class ExtractionResult:
    extractions: list[Extraction] = field(default_factory=list)
    attempted: int = 0
    failed: int = 0
    not_mentioned: int = 0
    skipped_over_cap: int = 0
    errors: list[str] = field(default_factory=list)


class FinancialExtractionWorker(BaseIntelligenceWorker):
    """LLM extraction of sentiment, direction, positions and levels with evidence.

    Calls are bounded by ``max_items`` (highest-engagement / most recent first) and ``concurrency``.
    Individual malformed outputs are counted, not fatal; if EVERY attempt fails the worker fails.
    """

    def __init__(self, service, repo, assets: dict, *, concurrency: int = 4, max_items: int = 300, logger=None):
        super().__init__("financial_extraction", logger)
        self.service, self.repo, self.assets = service, repo, assets
        self.concurrency, self.max_items = concurrency, max_items

    async def run(self, payload: list, context: dict) -> ExtractionResult:
        pairs = [(r, s) for r in payload if r.usable_for_features for s in r.asset_symbols]
        far = datetime(1970, 1, 1, tzinfo=UTC)
        pairs.sort(key=lambda p: (-p[0].engagement_weight, -(p[0].event_time or far).timestamp()))
        result = ExtractionResult(skipped_over_cap=max(0, len(pairs) - self.max_items))
        pairs = pairs[: self.max_items]
        sem = asyncio.Semaphore(self.concurrency)

        async def one(rec, symbol):
            async with sem:
                return await self.service.extract(rec, symbol, self.assets[symbol].name)

        outcomes = await asyncio.gather(*[one(r, s) for r, s in pairs], return_exceptions=True)
        for out in outcomes:
            result.attempted += 1
            if isinstance(out, BaseException):
                result.failed += 1
                if len(result.errors) < 5:
                    result.errors.append(f"{type(out).__name__}: {out}")
            elif out is None:
                result.not_mentioned += 1
            else:
                result.extractions.append(out)
        if result.attempted and result.failed == result.attempted:
            raise IntegrationError(f"all {result.attempted} extractions failed; first error: {result.errors[0]}")
        if result.extractions:
            await self.repo.upsert("intelligence_extractions",
                                   [e.to_row(context.get("run_id")) for e in result.extractions])
        for k in ("attempted", "failed", "not_mentioned", "skipped_over_cap"):
            self.metric(k, getattr(result, k))
        self.metric("extracted", len(result.extractions))
        return result


@dataclass
class AggregationInput:
    records: list
    scores: list[SentimentScore]
    extractions: list[Extraction]
    calendar: object


@dataclass
class AggregationResult:
    rows: pd.DataFrame
    window: pd.DataFrame            # per (symbol, source_type) over the whole collection window
    by_source: pd.DataFrame         # per (symbol, source) over the whole window
    daily: pd.DataFrame             # per (symbol, bucket_date, scope)


ROW_COLUMNS = ["record_id", "symbol", "source", "source_type", "event_time", "bucket_date", "author_id",
               "sentiment_score", "score_source", "weight", "engagement", "relevance"]


class SignalAggregationWorker(BaseIntelligenceWorker):
    """Per-record sentiment rows -> time-bucketed aggregates. Missing sentiment stays NaN; mentions are still counted."""

    def __init__(self, repo, logger=None):
        super().__init__("signal_aggregation", logger)
        self.repo = repo

    def validate(self, payload) -> None:
        super().validate(payload)
        if not isinstance(payload, AggregationInput):
            raise WorkerValidationError("expected AggregationInput")

    async def run(self, payload: AggregationInput, context: dict) -> AggregationResult:
        llm = {(e.record_id, e.symbol): e.sentiment_score for e in payload.extractions}
        fin = {(s.record_id, s.symbol): s.score for s in payload.scores}
        rows = []
        for rec in payload.records:
            if not rec.usable_for_features or rec.event_time is None:
                continue
            bucket = pd.Timestamp(payload.calendar.bucket_date(rec.event_time))
            for sym in rec.asset_symbols:
                key = (rec.record_id, sym)
                if key in llm:
                    score, src = llm[key], "llm"
                elif key in fin:
                    score, src = fin[key], "finbert"
                else:
                    score, src = np.nan, None
                rows.append({"record_id": rec.record_id, "symbol": sym, "source": rec.source,
                             "source_type": rec.source_type, "event_time": rec.event_time, "bucket_date": bucket,
                             "author_id": rec.author_id, "sentiment_score": score, "score_source": src,
                             "weight": rec.engagement_weight, "engagement": rec.engagement,
                             "relevance": getattr(rec, "relevance", None)})
        df = pd.DataFrame(rows, columns=ROW_COLUMNS)
        window = summarize_sentiment(df, ["symbol", "source_type"])
        by_source = summarize_sentiment(df, ["symbol", "source_type", "source"])
        daily = pd.concat([
            summarize_sentiment(df[df.source_type == "social"], ["symbol", "bucket_date"]).assign(scope="social"),
            summarize_sentiment(df[df.source_type == "news"], ["symbol", "bucket_date"]).assign(scope="news"),
            summarize_sentiment(df[df.source_type == "social"], ["symbol", "bucket_date", "source"])
            .assign(scope=lambda x: "social:" + x["source"].astype(str)) if len(df) else pd.DataFrame(),
        ], ignore_index=True) if len(df) else pd.DataFrame(columns=["symbol", "bucket_date", "scope"])
        if len(daily):
            await self.repo.upsert("sentiment_aggregates", [
                {"run_id": context.get("run_id"), "asset_symbol": r["symbol"], "bucket_date": r["bucket_date"].date(),
                 "scope": r["scope"], "mention_count": int(r["mention_count"]),
                 "metrics": to_json_safe({k: r[k] for k in daily.columns if k not in ("symbol", "bucket_date", "scope", "source")})}
                for r in daily.to_dict("records")])
        self.metric("rows", len(df))
        self.metric("unscored_rows", int(df["sentiment_score"].isna().sum()) if len(df) else 0)
        return AggregationResult(df, window, by_source, daily)
