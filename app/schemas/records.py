"""Plain-dataclass domain records passed between workers (framework-free, easy to test)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.utils import to_json_safe


@dataclass
class IngestionRequest:
    exchange: Any                      # ExchangeConfig
    assets: list                       # list[AssetConfig]
    window_start: datetime
    window_end: datetime
    max_items: int = 500


@dataclass
class IngestionResult:
    source: str
    kind: str                          # "social" | "news" | "market"
    records: list
    meta: dict = field(default_factory=dict)


@dataclass
class SocialPost:
    source: str
    source_post_id: str
    text: str
    posted_at: datetime | None
    author_id: str | None = None
    author_followers: int | None = None
    likes: int | None = None
    reposts: int | None = None
    views: int | None = None
    url: str | None = None
    language: str | None = None
    text_raw: str | None = None
    asset_symbols: list[str] = field(default_factory=list)
    engagement_weight: float = 1.0
    engagement_missing: bool = True
    usable_for_features: bool = True
    duplicate_of: str | None = None
    duplicate_count: int = 0
    provenance: dict = field(default_factory=dict)
    raw: dict | None = None
    source_type: str = "social"

    def __post_init__(self):
        if self.text_raw is None:
            self.text_raw = self.text

    @property
    def record_id(self) -> str:
        return f"{self.source}:{self.source_post_id}"

    @property
    def event_time(self) -> datetime | None:
        return self.posted_at

    @property
    def engagement(self) -> float | None:
        if self.likes is None and self.reposts is None:
            return None
        return float((self.likes or 0) + (self.reposts or 0))

    def to_row(self) -> dict:
        return {
            "source": self.source, "source_post_id": self.source_post_id,
            "text_raw": self.text_raw, "text_clean": self.text,
            "asset_symbols": list(self.asset_symbols), "author_id": self.author_id,
            "author_followers": self.author_followers, "likes": self.likes,
            "reposts": self.reposts, "views": self.views, "url": self.url,
            "language": self.language, "posted_at": self.posted_at,
            "engagement_weight": self.engagement_weight, "usable_for_features": self.usable_for_features,
            "duplicate_of": self.duplicate_of,
            "retrieved_at": self.provenance.get("retrieved_at"),
            "apify_actor_id": self.provenance.get("actor_id"),
            "apify_run_id": self.provenance.get("apify_run_id"),
            "apify_dataset_id": self.provenance.get("dataset_id"),
            "query": self.provenance.get("query"),
            "raw": to_json_safe(self.raw),
        }


@dataclass
class NewsArticle:
    source: str                        # "exa"
    source_post_id: str                # dedup key
    url: str
    title: str
    text: str
    published_at: datetime | None
    retrieved_at: datetime | None = None
    summary: str | None = None
    relevance: float | None = None
    source_domain: str | None = None
    query_symbol: str | None = None
    asset_symbols: list[str] = field(default_factory=list)
    usable_for_features: bool = True
    duplicate_of: str | None = None
    duplicate_count: int = 0
    engagement_weight: float = 1.0
    provenance: dict = field(default_factory=dict)
    raw: dict | None = None
    author_id: str | None = None
    source_type: str = "news"
    text_raw: str | None = None

    def __post_init__(self):
        if self.text_raw is None:
            self.text_raw = self.text

    @property
    def record_id(self) -> str:
        return f"{self.source}:{self.source_post_id}"

    @property
    def event_time(self) -> datetime | None:
        return self.published_at

    @property
    def engagement(self) -> float | None:
        return None

    def to_row(self) -> dict:
        return {
            "dedup_key": self.source_post_id, "url": self.url, "title": self.title,
            "text_clean": self.text, "summary": self.summary, "published_at": self.published_at,
            "retrieved_at": self.retrieved_at, "relevance": self.relevance,
            "source_domain": self.source_domain, "asset_symbols": list(self.asset_symbols),
            "query_symbol": self.query_symbol or "", "usable_for_features": self.usable_for_features,
            "exa_id": self.provenance.get("exa_id"), "query": self.provenance.get("query"),
            "raw": to_json_safe(self.raw),
        }


@dataclass
class MarketBar:
    symbol: str
    session_date: date
    ts: datetime                       # session close, UTC
    open: float
    high: float
    low: float
    close: float
    volume: float
    adj_close: float | None = None
    provider: str = "unknown"
    interval: str = "1d"

    def to_row(self) -> dict:
        return {"symbol": self.symbol, "interval": self.interval, "provider": self.provider,
                "session_date": self.session_date, "ts": self.ts, "open": self.open, "high": self.high,
                "low": self.low, "close": self.close, "adj_close": self.adj_close, "volume": self.volume}


@dataclass
class Extraction:
    record_id: str
    source_type: str
    source: str
    symbol: str
    asset_name: str | None
    sentiment: str
    sentiment_score: float
    direction: str
    position: dict | None
    support_levels: list[float]
    resistance_levels: list[float]
    target_price: float | None
    confidence: float
    evidence_text: str | None
    source_timestamp: datetime | None
    model_name: str
    extracted_at: datetime
    guard_notes: list[str] = field(default_factory=list)

    def to_row(self, run_id: str | None = None) -> dict:
        payload = {"asset_symbol": self.symbol, "asset_name": self.asset_name, "sentiment": self.sentiment,
                   "sentiment_score": self.sentiment_score, "direction": self.direction,
                   "position": self.position, "support_levels": self.support_levels,
                   "resistance_levels": self.resistance_levels, "target_price": self.target_price,
                   "confidence": self.confidence, "evidence_text": self.evidence_text,
                   "source_id": self.record_id, "guard_notes": self.guard_notes}
        return {"run_id": run_id, "source_type": self.source_type, "source_id": self.record_id,
                "source": self.source, "asset_symbol": self.symbol, "model_name": self.model_name,
                "sentiment_label": self.sentiment, "sentiment_score": self.sentiment_score,
                "direction": self.direction, "confidence": self.confidence,
                "evidence_text": self.evidence_text, "source_timestamp": self.source_timestamp,
                "extracted_at": self.extracted_at, "payload": to_json_safe(payload)}


@dataclass
class SentimentScore:
    record_id: str
    source_type: str
    symbol: str
    score: float
    label: str
    model: str


@dataclass
class FeatureRow:
    symbol: str
    as_of_date: date
    cutoff_at: datetime
    close: float
    features: dict
    coverage: dict = field(default_factory=dict)


@dataclass
class Prediction:
    run_id: str
    symbol: str
    as_of_date: date
    cutoff_at: datetime
    target_session_date: date
    current_price: float
    predicted_return: float
    predicted_price: float
    model_name: str
    model_version: str
    feature_schema_version: str
    created_at: datetime

    @property
    def prediction_key(self) -> str:
        return f"{self.symbol}|{self.cutoff_at.isoformat()}|{self.model_version}"

    def to_row(self) -> dict:
        return {"prediction_key": self.prediction_key, "run_id": self.run_id, "symbol": self.symbol,
                "as_of_date": self.as_of_date, "cutoff_at": self.cutoff_at,
                "target_session_date": self.target_session_date, "current_price": self.current_price,
                "predicted_return": self.predicted_return, "predicted_price": self.predicted_price,
                "model_name": self.model_name, "model_version": self.model_version,
                "feature_schema_version": self.feature_schema_version, "created_at": self.created_at}


def stable_id(*parts: Any) -> str:
    """Deterministic short id so re-ingesting the same content stays idempotent."""
    digest = hashlib.sha256("|".join("" if p is None else str(p) for p in parts).encode("utf-8")).hexdigest()
    return digest[:20]
