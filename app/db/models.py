"""SQLAlchemy 2.0 models. Raw, normalized, derived and evaluated data live in separate tables.

All timestamps are timezone-aware UTC. Exchange timezone/calendar metadata lives in ``exchanges``.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JsonType = JSON().with_variant(JSONB(), "postgresql")
TS = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class Exchange(Base):
    __tablename__ = "exchanges"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    timezone: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str | None] = mapped_column(String(8))
    close_time: Mapped[str | None] = mapped_column(String(8))
    holidays: Mapped[list | None] = mapped_column(JsonType)


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str | None] = mapped_column(String(128))
    exchange_code: Mapped[str | None] = mapped_column(String(16), index=True)
    currency: Mapped[str | None] = mapped_column(String(8))


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True)
    exchange_code: Mapped[str | None] = mapped_column(String(16), index=True)
    mode: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str | None] = mapped_column(String(16))
    config: Mapped[dict | None] = mapped_column(JsonType)
    started_at: Mapped[datetime | None] = mapped_column(TS)
    finished_at: Mapped[datetime | None] = mapped_column(TS)
    summary: Mapped[dict | None] = mapped_column(JsonType)
    error: Mapped[str | None] = mapped_column(Text)


class WorkerRun(Base):
    __tablename__ = "worker_runs"
    __table_args__ = (UniqueConstraint("run_id", "worker_name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("pipeline_runs.run_id"), index=True)
    worker_name: Mapped[str] = mapped_column(String(96))
    status: Mapped[str | None] = mapped_column(String(16))
    started_at: Mapped[datetime | None] = mapped_column(TS)
    duration_s: Mapped[float | None] = mapped_column(Float)
    attempts: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JsonType)


class SocialPostRow(Base):
    __tablename__ = "social_posts"
    __table_args__ = (UniqueConstraint("source", "source_post_id"), Index("ix_social_posted_at", "posted_at"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(48))
    source_post_id: Mapped[str] = mapped_column(String(128))
    text_raw: Mapped[str | None] = mapped_column(Text)
    text_clean: Mapped[str | None] = mapped_column(Text)
    asset_symbols: Mapped[list | None] = mapped_column(JsonType)
    author_id: Mapped[str | None] = mapped_column(String(128))
    author_followers: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    reposts: Mapped[int | None] = mapped_column(Integer)
    views: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(16))
    posted_at: Mapped[datetime | None] = mapped_column(TS)
    engagement_weight: Mapped[float | None] = mapped_column(Float)
    usable_for_features: Mapped[bool | None] = mapped_column(Boolean)
    duplicate_of: Mapped[str | None] = mapped_column(String(160))
    retrieved_at: Mapped[datetime | None] = mapped_column(TS)
    apify_actor_id: Mapped[str | None] = mapped_column(String(128))
    apify_run_id: Mapped[str | None] = mapped_column(String(64))
    apify_dataset_id: Mapped[str | None] = mapped_column(String(64))
    query: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict | None] = mapped_column(JsonType)


class NewsArticleRow(Base):
    __tablename__ = "news_articles"
    __table_args__ = (UniqueConstraint("dedup_key", "query_symbol"), Index("ix_news_published", "published_at"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dedup_key: Mapped[str] = mapped_column(String(64))
    query_symbol: Mapped[str] = mapped_column(String(32), default="")
    url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    text_clean: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(TS)   # publication time (may be unknown)
    retrieved_at: Mapped[datetime | None] = mapped_column(TS)   # when WE fetched it - never conflated
    relevance: Mapped[float | None] = mapped_column(Float)
    source_domain: Mapped[str | None] = mapped_column(String(128))
    asset_symbols: Mapped[list | None] = mapped_column(JsonType)
    usable_for_features: Mapped[bool | None] = mapped_column(Boolean)
    exa_id: Mapped[str | None] = mapped_column(String(128))
    query: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict | None] = mapped_column(JsonType)


class MarketBarRow(Base):
    __tablename__ = "market_bars"
    __table_args__ = (UniqueConstraint("symbol", "interval", "ts", "provider"), Index("ix_bars_symbol_date", "symbol", "session_date"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    interval: Mapped[str] = mapped_column(String(8))
    provider: Mapped[str] = mapped_column(String(32))
    session_date: Mapped[date] = mapped_column(Date)
    ts: Mapped[datetime] = mapped_column(TS)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    adj_close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)


class IntelligenceExtraction(Base):
    __tablename__ = "intelligence_extractions"
    __table_args__ = (UniqueConstraint("source_type", "source_id", "asset_symbol", "model_name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source_type: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[str] = mapped_column(String(160))
    source: Mapped[str | None] = mapped_column(String(48))
    asset_symbol: Mapped[str] = mapped_column(String(32), index=True)
    model_name: Mapped[str] = mapped_column(String(96))
    sentiment_label: Mapped[str | None] = mapped_column(String(16))
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    direction: Mapped[str | None] = mapped_column(String(16))
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence_text: Mapped[str | None] = mapped_column(Text)
    source_timestamp: Mapped[datetime | None] = mapped_column(TS)
    extracted_at: Mapped[datetime | None] = mapped_column(TS)
    payload: Mapped[dict | None] = mapped_column(JsonType)


class SentimentAggregate(Base):
    __tablename__ = "sentiment_aggregates"
    __table_args__ = (UniqueConstraint("asset_symbol", "bucket_date", "scope"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    asset_symbol: Mapped[str] = mapped_column(String(32), index=True)
    bucket_date: Mapped[date] = mapped_column(Date)
    scope: Mapped[str] = mapped_column(String(64))          # social | news | social:<source>
    mention_count: Mapped[int | None] = mapped_column(Integer)
    metrics: Mapped[dict | None] = mapped_column(JsonType)


class FeatureSnapshot(Base):
    __tablename__ = "feature_snapshots"
    __table_args__ = (UniqueConstraint("run_id", "asset_symbol"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    asset_symbol: Mapped[str] = mapped_column(String(32))
    as_of_date: Mapped[date | None] = mapped_column(Date)
    cutoff_at: Mapped[datetime | None] = mapped_column(TS)
    schema_version: Mapped[str | None] = mapped_column(String(16))
    features: Mapped[dict | None] = mapped_column(JsonType)
    coverage: Mapped[dict | None] = mapped_column(JsonType)


class PredictionRow(Base):
    __tablename__ = "predictions"
    __table_args__ = (UniqueConstraint("prediction_key"), Index("ix_pred_symbol_cutoff", "symbol", "cutoff_at"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_key: Mapped[str] = mapped_column(String(160))
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    as_of_date: Mapped[date | None] = mapped_column(Date)
    cutoff_at: Mapped[datetime | None] = mapped_column(TS)
    target_session_date: Mapped[date | None] = mapped_column(Date)
    current_price: Mapped[float | None] = mapped_column(Float)
    predicted_return: Mapped[float | None] = mapped_column(Float)
    predicted_price: Mapped[float | None] = mapped_column(Float)
    model_name: Mapped[str | None] = mapped_column(String(96))
    model_version: Mapped[str | None] = mapped_column(String(64))
    feature_schema_version: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime | None] = mapped_column(TS)


class PredictionEvaluation(Base):
    __tablename__ = "prediction_evaluations"
    __table_args__ = (UniqueConstraint("prediction_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_key: Mapped[str] = mapped_column(String(160))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    actual_session_date: Mapped[date | None] = mapped_column(Date)
    actual_price: Mapped[float | None] = mapped_column(Float)
    actual_return: Mapped[float | None] = mapped_column(Float)
    predicted_return: Mapped[float | None] = mapped_column(Float)
    abs_error: Mapped[float | None] = mapped_column(Float)
    baseline_abs_error: Mapped[float | None] = mapped_column(Float)   # zero-return (previous close) baseline
    directional_correct: Mapped[bool | None] = mapped_column(Boolean)
    evaluated_at: Mapped[datetime | None] = mapped_column(TS)


TABLES = {m.__tablename__: m for m in (
    Exchange, Asset, PipelineRun, WorkerRun, SocialPostRow, NewsArticleRow, MarketBarRow,
    IntelligenceExtraction, SentimentAggregate, FeatureSnapshot, PredictionRow, PredictionEvaluation)}
