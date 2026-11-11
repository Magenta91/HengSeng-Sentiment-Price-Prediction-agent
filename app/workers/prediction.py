from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from app.core.exceptions import ModelArtifactError, WorkerValidationError
from app.core.specialized_workers import BasePredictionWorker
from app.ml.evaluation import baseline_improvement, regression_metrics
from app.ml.feature_schema import (
    ALL_FEATURES,
    FEATURE_SCHEMA_VERSION,
    MARKET_FEATURES,
    MIN_HISTORY_SESSIONS,
    FeatureSchemaError,
    add_target,
    build_feature_frame,
    price_series,
)
from app.repositories.base import Op
from app.schemas.records import FeatureRow, Prediction
from app.services.market_frames import bars_to_frame
from app.utils import to_json_safe, utcnow

MAX_STALE_DAYS = 5


@dataclass
class FeaturePayload:
    bars_by_symbol: dict
    rows: pd.DataFrame | None
    calendar: object
    now: datetime


@dataclass
class FeatureResult:
    rows: list[FeatureRow] = field(default_factory=list)
    skipped: dict = field(default_factory=dict)


class FeatureEngineeringWorker(BasePredictionWorker):
    """Builds inference features with EXACTLY the shared training feature pipeline (app.ml.feature_schema)."""

    def __init__(self, repo, logger=None):
        super().__init__("feature_engineering", logger)
        self.repo = repo

    def validate(self, payload) -> None:
        super().validate(payload)
        if not isinstance(payload, FeaturePayload):
            raise WorkerValidationError("expected FeaturePayload")

    async def run(self, payload: FeaturePayload, context: dict) -> FeatureResult:
        cal, result = payload.calendar, FeatureResult()
        last_complete = pd.Timestamp(cal.last_complete_session(payload.now))
        rows = payload.rows
        for symbol, bars in payload.bars_by_symbol.items():
            df = bars_to_frame(bars)
            df = df[df["session_date"] <= last_complete]      # drop the still-open session
            if len(df) < MIN_HISTORY_SESSIONS:
                result.skipped[symbol] = f"only {len(df)} complete sessions (< {MIN_HISTORY_SESSIONS})"
                continue
            if (last_complete - df["session_date"].iloc[-1]).days > MAX_STALE_DAYS:
                result.skipped[symbol] = f"latest bar {df['session_date'].iloc[-1].date()} is stale"
                continue
            social = news = None
            if rows is not None and len(rows):
                social = rows[(rows.symbol == symbol) & (rows.source_type == "social")]
                news = rows[(rows.symbol == symbol) & (rows.source_type == "news")]
            latest = build_feature_frame(df, social, news).iloc[-1]
            as_of = latest["session_date"].date()
            result.rows.append(FeatureRow(
                symbol=symbol, as_of_date=as_of, cutoff_at=cal.session_close_utc(as_of), close=float(latest["close"]),
                features={c: float(latest[c]) for c in ALL_FEATURES},
                coverage={"social_posts": int(latest["social_mention_count"]), "news_articles": int(latest["news_article_count"]),
                          "has_social": bool(latest["social_has_data"]), "has_news": bool(latest["news_has_data"])}))
        if result.rows:
            await self.repo.upsert("feature_snapshots", [
                {"run_id": context.get("run_id"), "asset_symbol": r.symbol, "as_of_date": r.as_of_date,
                 "cutoff_at": r.cutoff_at, "schema_version": FEATURE_SCHEMA_VERSION,
                 "features": to_json_safe(r.features), "coverage": r.coverage} for r in result.rows])
        self.metric("symbols", len(result.rows))
        self.metric("skipped", len(result.skipped))
        return result


@dataclass
class PredictionResult:
    predictions: list[Prediction] = field(default_factory=list)
    skipped: dict = field(default_factory=dict)


class PredictionWorker(BasePredictionWorker):
    """Inference only: uses the preloaded model; never trains."""

    def __init__(self, model_service, repo, calendar, logger=None):
        super().__init__("prediction", logger)
        self.model, self.repo, self.calendar = model_service, repo, calendar

    def validate(self, payload) -> None:
        super().validate(payload)
        if not self.model.available:
            raise ModelArtifactError(self.model.unavailable_reason or "model unavailable")

    async def run(self, payload: list[FeatureRow], context: dict) -> PredictionResult:
        result, meta = PredictionResult(), self.model.metadata
        for fr in payload:
            frame = pd.DataFrame([fr.features])
            try:
                ret = float(self.model.predict(frame)[0])
            except FeatureSchemaError as exc:
                result.skipped[fr.symbol] = str(exc)
                continue
            result.predictions.append(Prediction(
                run_id=context.get("run_id", ""), symbol=fr.symbol, as_of_date=fr.as_of_date, cutoff_at=fr.cutoff_at,
                target_session_date=self.calendar.next_trading_day(fr.as_of_date), current_price=fr.close,
                predicted_return=ret, predicted_price=fr.close * (1 + ret), model_name=meta["model_name"],
                model_version=str(meta["model_version"]), feature_schema_version=meta["feature_schema_version"],
                created_at=utcnow()))
        if result.predictions:
            await self.repo.upsert("predictions", [p.to_row() for p in result.predictions])
        self.metric("predicted", len(result.predictions))
        return result


class PredictionEvaluationWorker(BasePredictionWorker):
    """Scores earlier predictions once the next actual session exists; compares with a zero-return
    (previous close) baseline. Uses the first bar AFTER the as-of date, so an unlisted holiday cannot misalign."""

    def __init__(self, repo, logger=None):
        super().__init__("prediction_evaluation", logger)
        self.repo = repo

    async def run(self, payload: dict, context: dict) -> dict:
        preds = await self.repo.select("predictions", where={"symbol": Op("in", list(payload))})
        done = {e["prediction_key"] for e in await self.repo.select("prediction_evaluations")}
        frames = {s: bars_to_frame(b) for s, b in payload.items()}
        new_rows = []
        for p in preds:
            if p["prediction_key"] in done or p.get("as_of_date") is None:
                continue
            df = frames[p["symbol"]]
            asof = pd.Timestamp(p["as_of_date"])
            base = df[df["session_date"] == asof]
            nxt = df[df["session_date"] > asof].head(1)
            if base.empty or nxt.empty:
                continue
            px = price_series(df)
            actual_ret = float(px[nxt.index[0]] / px[base.index[0]] - 1)
            pr = float(p["predicted_return"])
            new_rows.append({
                "prediction_key": p["prediction_key"], "symbol": p["symbol"],
                "actual_session_date": nxt["session_date"].iloc[0].date(), "actual_price": float(nxt["close"].iloc[0]),
                "actual_return": actual_ret, "predicted_return": pr, "abs_error": abs(pr - actual_ret),
                "baseline_abs_error": abs(actual_ret),
                "directional_correct": None if actual_ret == 0 else bool(np.sign(pr) == np.sign(actual_ret)),
                "evaluated_at": utcnow()})
        if new_rows:
            await self.repo.upsert("prediction_evaluations", new_rows)
        every = await self.repo.select("prediction_evaluations", where={"symbol": Op("in", list(payload))})
        summary = {"n": len(every), "new": len(new_rows)}
        if every:
            m = regression_metrics([e["actual_return"] for e in every], [e["predicted_return"] for e in every])
            b = regression_metrics([e["actual_return"] for e in every], [0.0] * len(every))
            summary.update({"model": m, "baseline_zero_return": b, **baseline_improvement(m, b)})
        self.metric("evaluated_new", len(new_rows))
        return {"new": new_rows, "all": every, "summary": summary}


class BacktestingWorker(BasePredictionWorker):
    """Re-scores the model on the locally available bars from the artifact's *test* period onward.

    Only meaningful for models whose features can be reproduced from prices alone (market_only); historical
    sentiment is not available locally, so sentiment models are reported as 'unavailable' rather than faked.
    """

    def __init__(self, model_service, logger=None):
        super().__init__("backtesting", logger)
        self.model = model_service

    async def run(self, payload: dict, context: dict) -> dict:
        meta = self.model.metadata
        test = (meta.get("date_ranges") or {}).get("test")
        if not test or not test[0]:
            return {"status": "unavailable", "reason": "model metadata has no held-out test period"}
        sentiment_cols = [c for c in self.model.feature_columns if c not in MARKET_FEATURES]
        if sentiment_cols:
            return {"status": "unavailable", "reason": "model uses sentiment features; historical sentiment is not stored locally"}
        start, per, pooled_t, pooled_p = pd.Timestamp(test[0]), {}, [], []
        for symbol, bars in payload.items():
            frame = add_target(build_feature_frame(bars_to_frame(bars)))
            sub = frame[frame["session_date"] >= start].dropna(subset=["next_return"] + self.model.feature_columns)
            if sub.empty:
                continue
            preds = self.model.predict(sub)
            per[symbol] = {"model": regression_metrics(sub["next_return"], preds),
                           "baseline_zero_return": regression_metrics(sub["next_return"], np.zeros(len(sub)))}
            pooled_t += list(sub["next_return"]); pooled_p += list(preds)
        if not pooled_t:
            return {"status": "insufficient_data", "reason": f"no local bars on/after test start {test[0]}"}
        m, b = regression_metrics(pooled_t, pooled_p), regression_metrics(pooled_t, [0.0] * len(pooled_t))
        return {"status": "ok", "test_start": test[0], "overall": {"model": m, "baseline_zero_return": b,
                                                                  **baseline_improvement(m, b)}, "per_symbol": per}
