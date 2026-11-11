"""Shared, versioned feature definitions.

This module is the single source of truth for feature names, ordering, units,
null handling and aggregation. It depends only on numpy/pandas so that the Colab
training notebook and the local application import *the same file*.

Conventions
-----------
* One call handles ONE symbol. ``session_date`` is the exchange-local trading date.
* Features on row ``t`` use only information available at the close of session ``t``.
* Missing sentiment stays NaN (never silently neutral). Counts are 0 when no data and a
  ``*_has_data`` indicator records coverage explicitly.
* Target ``next_return = price[t+1] / price[t] - 1`` uses the adjusted price series when
  available, so splits/dividends do not create fake returns.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

import numpy as np
import pandas as pd

FEATURE_SCHEMA_VERSION = "1.0.0"
TARGET_NAME = "next_return"
HORIZON = "next_trading_session"
SENTIMENT_LABEL_THRESHOLD = 0.15   # |score| above this is bullish/bearish; otherwise neutral
ENGAGEMENT_CAP = 10_000.0          # bounds the influence of viral posts
MIN_HISTORY_SESSIONS = 40          # warm-up needed for the longest indicator window

MARKET_FEATURES: list[str] = [
    "ret_1", "ret_2", "ret_3", "ret_5", "ret_10",
    "volume_change_1", "hl_range", "oc_return",
    "rsi_14", "macd_pct", "macd_signal_pct", "macd_hist_pct",
    "ma5_dist", "ma10_dist", "ma20_dist",
    "volatility_10", "volatility_20", "day_of_week",
]
SOCIAL_FEATURES: list[str] = [
    "social_sentiment_mean", "social_sentiment_median",
    "social_bullish_ratio", "social_bearish_ratio", "social_neutral_ratio",
    "social_mention_count", "social_unique_author_count", "social_engagement_sum",
    "social_engagement_weighted_sentiment", "social_sentiment_change", "social_has_data",
]
NEWS_FEATURES: list[str] = [
    "news_sentiment_mean", "news_article_count", "news_relevance_mean",
    "news_sentiment_change", "news_has_data",
]
ALL_FEATURES: list[str] = MARKET_FEATURES + SOCIAL_FEATURES + NEWS_FEATURES
FEATURE_SETS: dict[str, list[str]] = {
    "market_only": MARKET_FEATURES,
    "market_sentiment": ALL_FEATURES,
}

SUMMARY_COLUMNS = [
    "mention_count", "scored_count", "unique_author_count", "engagement_sum",
    "sentiment_mean", "sentiment_median", "bullish_ratio", "bearish_ratio",
    "neutral_ratio", "weighted_sentiment", "relevance_mean",
]


class FeatureSchemaError(ValueError):
    """Raised when features are missing or incompatible with the schema."""


# --------------------------------------------------------------------------- sentiment helpers
def sentiment_label(score: float | None) -> str | None:
    if score is None or (isinstance(score, float) and math.isnan(score)):
        return None
    if score > SENTIMENT_LABEL_THRESHOLD:
        return "bullish"
    if score < -SENTIMENT_LABEL_THRESHOLD:
        return "bearish"
    return "neutral"


def engagement_weight(likes: int | None, reposts: int | None) -> tuple[float, bool]:
    """Bounded log-scaled weight ``1 + log1p(min(likes + reposts, cap))``.

    Returns ``(weight, engagement_missing)``. Missing metrics give weight 1.0 (the
    unweighted baseline) and are flagged, not treated as zero engagement. Followers are
    intentionally excluded; test them as a separate experiment.
    """
    if likes is None and reposts is None:
        return 1.0, True
    total = max(likes or 0, 0) + max(reposts or 0, 0)
    return 1.0 + math.log1p(min(float(total), ENGAGEMENT_CAP)), False


def summarize_sentiment(rows: pd.DataFrame | None, group_cols: Sequence[str]) -> pd.DataFrame:
    """Aggregate per-record sentiment rows into per-group statistics.

    Expected columns: ``sentiment_score`` (NaN when unscored) and optionally ``weight``,
    ``author_id``, ``engagement``, ``relevance``. Mention counts include unscored rows;
    sentiment statistics use scored rows only.
    """
    group_cols = list(group_cols)
    if rows is None or len(rows) == 0:
        return pd.DataFrame(columns=group_cols + SUMMARY_COLUMNS)
    records = []
    for key, g in rows.groupby(group_cols, sort=True):
        key = key if isinstance(key, tuple) else (key,)
        rec = dict(zip(group_cols, key))
        scores = g["sentiment_score"].astype(float)
        mask = scores.notna()
        s = scores[mask]
        n_scored = int(mask.sum())
        weights = g["weight"].astype(float)[mask] if "weight" in g else pd.Series(1.0, index=s.index)
        rec["mention_count"] = len(g)
        rec["scored_count"] = n_scored
        rec["unique_author_count"] = int(g["author_id"].nunique()) if "author_id" in g else 0
        rec["engagement_sum"] = float(g["engagement"].fillna(0).sum()) if "engagement" in g else 0.0
        if n_scored:
            rec["sentiment_mean"] = float(s.mean())
            rec["sentiment_median"] = float(s.median())
            rec["bullish_ratio"] = float((s > SENTIMENT_LABEL_THRESHOLD).mean())
            rec["bearish_ratio"] = float((s < -SENTIMENT_LABEL_THRESHOLD).mean())
            rec["neutral_ratio"] = 1.0 - rec["bullish_ratio"] - rec["bearish_ratio"]
            wsum = float(weights.sum())
            rec["weighted_sentiment"] = float((s * weights).sum() / wsum) if wsum > 0 else float(s.mean())
        else:
            for col in ("sentiment_mean", "sentiment_median", "bullish_ratio", "bearish_ratio",
                        "neutral_ratio", "weighted_sentiment"):
                rec[col] = np.nan
        rel = g["relevance"] if "relevance" in g else None
        rec["relevance_mean"] = float(rel.mean()) if rel is not None and rel.notna().any() else np.nan
        records.append(rec)
    return pd.DataFrame(records)


# --------------------------------------------------------------------------- market features
def price_series(bars: pd.DataFrame) -> pd.Series:
    """Adjusted close when available (falling back per-row to close), else close."""
    if "adj_close" in bars.columns and bars["adj_close"].notna().any():
        return bars["adj_close"].where(bars["adj_close"].notna(), bars["close"]).astype(float)
    return bars["close"].astype(float)


def _naive_dates(series: pd.Series) -> pd.Series:
    s = pd.to_datetime(series)
    if getattr(s.dt, "tz", None) is not None:
        s = s.dt.tz_localize(None)
    return s.dt.normalize()


def compute_market_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Technical features per session from daily OHLCV bars of a single symbol."""
    required = {"session_date", "open", "high", "low", "close", "volume"}
    missing = required - set(bars.columns)
    if missing:
        raise FeatureSchemaError(f"bars missing columns: {sorted(missing)}")
    df = bars.copy()
    df["session_date"] = _naive_dates(df["session_date"])
    df = df.sort_values("session_date").drop_duplicates("session_date", keep="last").reset_index(drop=True)
    px = price_series(df)
    close = df["close"].astype(float)
    out = pd.DataFrame({"session_date": df["session_date"], "close": close, "price": px})
    for k in (1, 2, 3, 5, 10):
        out[f"ret_{k}"] = px / px.shift(k) - 1
    vol = df["volume"].astype(float)
    out["volume_change_1"] = vol / vol.shift(1).replace(0, np.nan) - 1
    out["hl_range"] = (df["high"].astype(float) - df["low"].astype(float)) / close
    out["oc_return"] = close / df["open"].astype(float) - 1

    delta = px.diff()
    avg_gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    out["rsi_14"] = 100 - 100 / (1 + avg_gain / avg_loss.replace(0, np.nan))
    out.loc[(avg_loss == 0) & (avg_gain > 0), "rsi_14"] = 100.0

    macd = px.ewm(span=12, adjust=False).mean() - px.ewm(span=26, adjust=False).mean()
    signal = macd.ewm(span=9, adjust=False).mean()
    out["macd_pct"] = macd / px
    out["macd_signal_pct"] = signal / px
    out["macd_hist_pct"] = (macd - signal) / px
    for w in (5, 10, 20):
        out[f"ma{w}_dist"] = px / px.rolling(w).mean() - 1
    r1 = px.pct_change()
    out["volatility_10"] = r1.rolling(10).std()
    out["volatility_20"] = r1.rolling(20).std()
    out["day_of_week"] = out["session_date"].dt.dayofweek.astype(float)
    return out


# --------------------------------------------------------------------------- full frame
_SOCIAL_RENAME = {
    "sentiment_mean": "social_sentiment_mean", "sentiment_median": "social_sentiment_median",
    "bullish_ratio": "social_bullish_ratio", "bearish_ratio": "social_bearish_ratio",
    "neutral_ratio": "social_neutral_ratio", "mention_count": "social_mention_count",
    "unique_author_count": "social_unique_author_count", "engagement_sum": "social_engagement_sum",
    "weighted_sentiment": "social_engagement_weighted_sentiment",
}
_NEWS_RENAME = {
    "sentiment_mean": "news_sentiment_mean", "mention_count": "news_article_count",
    "relevance_mean": "news_relevance_mean",
}


def _merge_block(frame: pd.DataFrame, rows: pd.DataFrame | None, prefix: str) -> pd.DataFrame:
    rename = _SOCIAL_RENAME if prefix == "social" else _NEWS_RENAME
    wanted = SOCIAL_FEATURES if prefix == "social" else NEWS_FEATURES
    if rows is not None and len(rows):
        r = rows.copy()
        r["bucket_date"] = _naive_dates(r["bucket_date"])
        agg = summarize_sentiment(r, ["bucket_date"]).rename(columns={"bucket_date": "session_date", **rename})
        keep = ["session_date"] + [c for c in agg.columns if c in wanted]
        frame = frame.merge(agg[keep], on="session_date", how="left")
    for col in wanted:
        if col not in frame.columns:
            frame[col] = np.nan
    count_col = "social_mention_count" if prefix == "social" else "news_article_count"
    frame[count_col] = frame[count_col].fillna(0.0)
    frame[f"{prefix}_has_data"] = (frame[count_col] > 0).astype(float)
    if prefix == "social":
        for col in ("social_unique_author_count", "social_engagement_sum"):
            frame[col] = frame[col].fillna(0.0)
    frame[f"{prefix}_sentiment_change"] = frame[f"{prefix}_sentiment_mean"].diff()
    return frame


def build_feature_frame(bars: pd.DataFrame, social_rows: pd.DataFrame | None = None,
                        news_rows: pd.DataFrame | None = None) -> pd.DataFrame:
    """Market + sentiment features for one symbol, columns in canonical order.

    ``social_rows`` / ``news_rows`` need ``bucket_date`` (exchange trading session) and the
    columns documented in :func:`summarize_sentiment`.
    """
    frame = compute_market_features(bars)
    frame = _merge_block(frame, social_rows, "social")
    frame = _merge_block(frame, news_rows, "news")
    return frame[["session_date", "close", "price"] + ALL_FEATURES]


def add_target(frame: pd.DataFrame) -> pd.DataFrame:
    """Append ``next_return`` and ``target_session_date`` (the label for row t looks at t+1)."""
    out = frame.copy()
    out[TARGET_NAME] = out["price"].shift(-1) / out["price"] - 1
    out["target_session_date"] = out["session_date"].shift(-1)
    return out


def select_features(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    """Return ``frame[columns]`` in exactly that order, failing loudly on mismatch."""
    columns = list(columns)
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise FeatureSchemaError(f"missing feature columns: {missing}")
    unknown = [c for c in columns if c not in ALL_FEATURES]
    if unknown:
        raise FeatureSchemaError(f"columns not in schema v{FEATURE_SCHEMA_VERSION}: {unknown}")
    return frame[columns].astype(float)
