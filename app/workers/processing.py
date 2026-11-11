from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import replace
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.core.specialized_workers import BaseProcessingWorker
from app.ml.feature_schema import engagement_weight
from app.schemas.records import stable_id
from app.utils import UTC, ensure_utc

_TAGS = re.compile(r"<[^>]+>")
_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")
_SPACES = re.compile(r"\s+")
_TRACKING = ("utm_", "fbclid", "gclid", "mc_", "ref")


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = _TAGS.sub(" ", text)
    text = unicodedata.normalize("NFKC", text)
    text = _ZERO_WIDTH.sub("", text)
    return _SPACES.sub(" ", text).strip()


def canonical_url(url: str) -> str:
    """Lower-case host, drop fragments, tracking params and trailing slash so one article = one key."""
    p = urlparse(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith(_TRACKING)])
    return urlunparse((p.scheme.lower(), p.netloc.lower().removeprefix("www."), p.path.rstrip("/"), "", query, ""))


class CleaningWorker(BaseProcessingWorker):
    """Strip HTML/zero-width characters, NFKC-normalise and collapse whitespace; drop empty texts."""

    def __init__(self, min_chars: int = 2, logger=None):
        super().__init__("cleaning", logger)
        self.min_chars = min_chars

    async def run(self, payload: list, context: dict) -> list:
        out = []
        for rec in payload:
            text = clean_text(rec.text)
            if len(text) >= self.min_chars:
                out.append(replace(rec, text=text))
        self.metric("dropped_empty", len(payload) - len(out))
        return out


class TimestampNormalizationWorker(BaseProcessingWorker):
    """Ensure UTC-aware times and enforce the collection window / no-future-data rule.

    Posts without a usable timestamp are dropped (they cannot be aligned to a session without
    guessing). Articles without a publication time are kept for display but excluded from
    features: retrieval time is NOT a substitute for publication time.
    """

    def __init__(self, window_start: datetime, window_end: datetime, future_tolerance: timedelta = timedelta(minutes=5),
                 logger=None):
        super().__init__("timestamp_normalization", logger)
        self.start, self.end, self.tol = window_start, window_end, future_tolerance

    async def run(self, payload: list, context: dict) -> list:
        out, no_ts, outside = [], 0, 0
        for rec in payload:
            if rec.source_type == "social":
                ts = ensure_utc(rec.posted_at)
                if ts is None:
                    no_ts += 1
                    continue
                if ts < self.start or ts > self.end + self.tol:
                    outside += 1
                    continue
                out.append(replace(rec, posted_at=ts, usable_for_features=True))
            else:
                ts = ensure_utc(rec.published_at)
                if ts is None:
                    out.append(replace(rec, published_at=None, usable_for_features=False))
                elif ts < self.start or ts > self.end + self.tol:
                    outside += 1
                else:
                    out.append(replace(rec, published_at=ts, usable_for_features=True))
        self.metric("dropped_no_timestamp", no_ts)
        self.metric("dropped_outside_window", outside)
        self.metric("articles_without_publication_time", sum(1 for r in out if not r.usable_for_features))
        return out


class DeduplicationWorker(BaseProcessingWorker):
    """Collapse duplicates but keep provenance: the earliest record survives, duplicate ids are listed."""

    def __init__(self, logger=None):
        super().__init__("deduplication", logger)

    @staticmethod
    def _key(rec) -> tuple:
        if rec.source_type == "news":
            return ("news", canonical_url(rec.url) if rec.url else stable_id(rec.title))
        body = _SPACES.sub(" ", rec.text.lower())
        return ("social", rec.source, rec.author_id or "", stable_id(body))

    async def run(self, payload: list, context: dict) -> list:
        far = datetime.max.replace(tzinfo=UTC)
        ordered = sorted(payload, key=lambda r: (r.event_time or far, r.record_id))
        kept: dict[tuple, object] = {}
        for rec in ordered:
            k = self._key(rec)
            first = kept.get(k)
            if first is None:
                kept[k] = replace(rec, provenance=dict(rec.provenance), asset_symbols=list(rec.asset_symbols))
                continue
            if rec.record_id != first.record_id:
                first.duplicate_count += 1
                first.provenance.setdefault("duplicate_ids", []).append(rec.record_id)
            first.asset_symbols = list(dict.fromkeys(first.asset_symbols + list(rec.asset_symbols)))
        result = list(kept.values())
        self.metric("duplicates_removed", len(payload) - len(result))
        return result


class TickerMappingWorker(BaseProcessingWorker):
    """Map records to canonical symbols. Posts that mention no configured asset are dropped."""

    def __init__(self, ticker_service, logger=None):
        super().__init__("ticker_mapping", logger)
        self.tickers = ticker_service

    async def run(self, payload: list, context: dict) -> list:
        out, dropped = [], 0
        for rec in payload:
            matched = self.tickers.symbols(rec.text)
            symbols = list(dict.fromkeys(list(rec.asset_symbols) + matched)) if rec.source_type == "news" else matched
            if not symbols:
                dropped += 1
                continue
            out.append(replace(rec, asset_symbols=symbols))
        self.metric("dropped_unmapped", dropped)
        return out


class EngagementNormalizationWorker(BaseProcessingWorker):
    """Bounded log-scaled engagement weights; missing metrics are flagged, never treated as zero."""

    def __init__(self, logger=None):
        super().__init__("engagement_normalization", logger)

    async def run(self, payload: list, context: dict) -> list:
        out = []
        for rec in payload:
            if rec.source_type == "social":
                w, missing = engagement_weight(rec.likes, rec.reposts)
                out.append(replace(rec, engagement_weight=w, engagement_missing=missing))
            else:
                out.append(rec)
        self.metric("posts_missing_engagement", sum(1 for r in out if r.source_type == "social" and r.engagement_missing))
        return out
