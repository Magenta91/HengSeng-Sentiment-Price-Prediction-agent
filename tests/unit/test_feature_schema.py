import math

import numpy as np
import pandas as pd

from app.ml.feature_schema import (
    ALL_FEATURES,
    MARKET_FEATURES,
    FeatureSchemaError,
    add_target,
    build_feature_frame,
    engagement_weight,
    select_features,
    summarize_sentiment,
)


def bars(n=80, seed=0):
    r = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(r.normal(0, 0.01, n)))
    return pd.DataFrame({"session_date": pd.bdate_range("2025-01-01", periods=n), "open": c * .999,
                         "high": c * 1.01, "low": c * .99, "close": c, "volume": r.integers(1e5, 1e6, n)})


def test_column_order_is_canonical():
    f = build_feature_frame(bars())
    assert list(f.columns) == ["session_date", "close", "price"] + ALL_FEATURES


def test_features_do_not_use_future_data():
    b = bars()
    full = build_feature_frame(b)
    truncated = build_feature_frame(b.iloc[:60])
    changed = b.copy()
    changed.loc[61:, ["open", "high", "low", "close"]] *= 3          # corrupt the future
    altered = build_feature_frame(changed)
    pd.testing.assert_frame_equal(full.iloc[:60][MARKET_FEATURES].reset_index(drop=True),
                                  truncated[MARKET_FEATURES].reset_index(drop=True))
    pd.testing.assert_frame_equal(full.iloc[:60][MARKET_FEATURES].reset_index(drop=True),
                                  altered.iloc[:60][MARKET_FEATURES].reset_index(drop=True))


def test_target_is_next_session_return():
    f = add_target(build_feature_frame(bars()))
    assert math.isclose(f["next_return"].iloc[10], f["price"].iloc[11] / f["price"].iloc[10] - 1)
    assert math.isnan(f["next_return"].iloc[-1])


def test_missing_sentiment_is_nan_not_neutral():
    f = build_feature_frame(bars())
    assert f["social_sentiment_mean"].isna().all()
    assert (f["social_mention_count"] == 0).all() and (f["social_has_data"] == 0).all()


def test_sentiment_aligns_to_bucket_and_marks_coverage():
    b = bars()
    day = b["session_date"].iloc[70]
    rows = pd.DataFrame({"bucket_date": [day, day], "sentiment_score": [0.8, np.nan], "weight": [2.0, 1.0],
                         "author_id": ["a", "b"], "engagement": [10.0, None]})
    f = build_feature_frame(b, rows).set_index("session_date")
    assert f.loc[day, "social_mention_count"] == 2 and f.loc[day, "social_has_data"] == 1
    assert math.isclose(f.loc[day, "social_sentiment_mean"], 0.8)              # unscored row excluded from the mean
    assert f.loc[b["session_date"].iloc[69], "social_has_data"] == 0


def test_summarize_ratios_and_weighting():
    rows = pd.DataFrame({"g": [1, 1, 1], "sentiment_score": [0.9, -0.9, 0.0], "weight": [3.0, 1.0, 1.0]})
    s = summarize_sentiment(rows, ["g"]).iloc[0]
    assert (s.bullish_ratio, s.bearish_ratio) == (1 / 3, 1 / 3) and math.isclose(s.neutral_ratio, 1 / 3)
    assert math.isclose(s.weighted_sentiment, (0.9 * 3 - 0.9) / 5)


def test_engagement_weight_bounded_and_missing_flagged():
    assert engagement_weight(None, None) == (1.0, True)
    w, missing = engagement_weight(10**9, 0)
    assert not missing and w < 12
    assert engagement_weight(0, 0)[0] == 1.0


def test_select_features_rejects_missing_or_unknown():
    f = build_feature_frame(bars())
    for cols in (["nope"], ["ret_1", "missing_col"]):
        try:
            select_features(f, cols)
        except FeatureSchemaError:
            continue
        raise AssertionError("expected FeatureSchemaError")
    assert list(select_features(f, ["rsi_14", "ret_1"]).columns) == ["rsi_14", "ret_1"]
