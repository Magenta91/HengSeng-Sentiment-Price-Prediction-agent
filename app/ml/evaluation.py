"""Chronological evaluation helpers shared by the Colab notebook and the local app."""
from __future__ import annotations

from collections.abc import Callable, Iterable

import numpy as np
import pandas as pd


def directional_accuracy(y_true: Iterable[float], y_pred: Iterable[float]) -> float | None:
    """Share of correct sign calls among rows where the actual move AND the prediction are non-zero.

    A zero prediction (e.g. the zero-return baseline) makes no directional call, so it yields ``None``
    rather than a misleading 0%.
    """
    yt, yp = np.asarray(list(y_true), dtype=float), np.asarray(list(y_pred), dtype=float)
    mask = (yt != 0) & (yp != 0)
    if not mask.any():
        return None
    return float((np.sign(yt[mask]) == np.sign(yp[mask])).mean())


def regression_metrics(y_true: Iterable[float], y_pred: Iterable[float]) -> dict:
    yt, yp = np.asarray(list(y_true), dtype=float), np.asarray(list(y_pred), dtype=float)
    if len(yt) == 0:
        return {"n": 0, "mae": None, "rmse": None, "directional_accuracy": None}
    err = yp - yt
    return {
        "n": len(yt),
        "mae": float(np.abs(err).mean()),
        "rmse": float(np.sqrt((err ** 2).mean())),
        "directional_accuracy": directional_accuracy(yt, yp),
    }


def baseline_improvement(model: dict, baseline: dict) -> dict:
    """Relative MAE/RMSE improvement over the baseline (positive = better than baseline)."""
    out = {}
    for key in ("mae", "rmse"):
        m, b = model.get(key), baseline.get(key)
        out[f"{key}_improvement_pct"] = None if not b or m is None else float((b - m) / b * 100.0)
    return out


def chronological_split(dates: Iterable, train_frac: float = 0.6, val_frac: float = 0.2) -> dict:
    """Split unique, sorted dates into train/validation/test blocks (never shuffled)."""
    uniq = np.array(sorted(pd.to_datetime(list(dates)).unique()))
    n = len(uniq)
    if n < 10:
        raise ValueError("need at least 10 distinct dates for a chronological split")
    i_train, i_val = int(n * train_frac), int(n * (train_frac + val_frac))
    return {
        "train": (uniq[0], uniq[i_train - 1]),
        "validation": (uniq[i_train], uniq[i_val - 1]),
        "test": (uniq[i_val], uniq[-1]),
    }


def walk_forward_date_splits(dates: Iterable, n_splits: int = 5, min_train_dates: int = 120,
                             gap_dates: int = 0) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window splits over unique dates; returns (train_dates, test_dates) pairs.

    Splitting on dates (not rows) keeps all tickers of a given day on the same side.
    """
    uniq = np.array(sorted(pd.to_datetime(list(dates)).unique()))
    n = len(uniq)
    if n < min_train_dates + n_splits:
        raise ValueError(f"{n} dates is not enough for {n_splits} splits with min_train={min_train_dates}")
    test_size = (n - min_train_dates) // n_splits
    splits = []
    for k in range(n_splits):
        test_start = min_train_dates + k * test_size
        test_end = n if k == n_splits - 1 else test_start + test_size
        train_end = test_start - gap_dates
        splits.append((uniq[:train_end], uniq[test_start:test_end]))
    return splits


def walk_forward_predict(model_factory: Callable[[], object], data: pd.DataFrame, feature_cols: list[str],
                         target_col: str, date_col: str, splits) -> pd.DataFrame:
    """Fit a FRESH model per fold on training dates only; predict the fold's test dates."""
    parts = []
    dcol = pd.to_datetime(data[date_col])
    for k, (train_dates, test_dates) in enumerate(splits):
        tr, te = data[dcol.isin(train_dates)], data[dcol.isin(test_dates)]
        model = model_factory()
        model.fit(tr[feature_cols], tr[target_col])
        out = te[[date_col, target_col] + ([c for c in ("symbol",) if c in te.columns])].copy()
        out["prediction"] = model.predict(te[feature_cols])
        out["fold"] = k
        parts.append(out)
    return pd.concat(parts, ignore_index=True)


def per_symbol_metrics(preds: pd.DataFrame, target_col: str, pred_col: str = "prediction") -> dict:
    result = {}
    if "symbol" in preds.columns:
        for sym, g in preds.groupby("symbol"):
            result[str(sym)] = regression_metrics(g[target_col], g[pred_col])
    return result
