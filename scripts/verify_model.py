"""Validate a model artifact against the feature schema:  python -m scripts.verify_model [model_dir]"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from app.ml.feature_schema import build_feature_frame
from app.services.model_service import ModelService


def main(directory: str) -> int:
    d = Path(directory)
    svc = ModelService.load(d / "xgboost_model.joblib", d / "model_metadata.json")
    if not svc.available:
        print(f"FAIL: {svc.unavailable_reason}")
        return 1
    print("artifact OK:", {k: v for k, v in svc.info().items() if k != "metrics"})
    r = np.random.default_rng(0)
    c = 100 * np.exp(np.cumsum(r.normal(0, 0.01, 120)))
    bars = pd.DataFrame({"session_date": pd.bdate_range("2025-01-01", periods=120), "open": c, "high": c * 1.01,
                         "low": c * .99, "close": c, "volume": r.integers(1e5, 1e6, 120)})
    pred = svc.predict(build_feature_frame(bars).tail(1))
    print(f"smoke prediction on synthetic bars: next_return={pred[0]:+.5f}  (shape/order check only)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "model_artifacts"))
