"""Builds a clearly-labelled DEMO model artifact from synthetic prices.

This exists only so the app can be exercised end-to-end without a Colab-trained model. It is a
separate, explicit script/function - the API never trains. Real artifacts come from the notebook.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app.core.config_models import ExchangesConfig
from app.demo.fixtures import DemoMarketProvider
from app.ml.evaluation import baseline_improvement, chronological_split, regression_metrics
from app.ml.feature_schema import FEATURE_SCHEMA_VERSION, HORIZON, MARKET_FEATURES, TARGET_NAME, add_target, build_feature_frame
from app.services.market_frames import bars_to_frame


async def build_demo_artifacts(out_dir: str | Path, config_path: str | Path = "config/exchanges.json") -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ex = ExchangesConfig.load(config_path).get("HKEX")
    cal, provider, frames = ex.calendar(), DemoMarketProvider(), []
    for a in ex.assets:
        bars = await provider.get_daily_bars(a.symbol, date(2023, 1, 1), date(2025, 12, 31), cal)
        f = add_target(build_feature_frame(bars_to_frame(bars))).assign(symbol=a.symbol)
        frames.append(f.dropna(subset=MARKET_FEATURES + [TARGET_NAME]))
    data = pd.concat(frames, ignore_index=True).sort_values("session_date")
    split = chronological_split(data["session_date"])
    train = data[data.session_date <= split["train"][1]]
    test = data[data.session_date >= split["test"][0]]
    model = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), Ridge(alpha=10.0))
    model.fit(train[MARKET_FEATURES], train[TARGET_NAME])
    preds = model.predict(test[MARKET_FEATURES])
    m = regression_metrics(test[TARGET_NAME], preds)
    b = regression_metrics(test[TARGET_NAME], np.zeros(len(test)))
    model_path = out / "xgboost_model.joblib"        # fixed filename the app loads by default
    joblib.dump(model, model_path)
    iso = lambda t: pd.Timestamp(t).date().isoformat()
    meta = {
        "demo": True, "model_name": "DEMO_ridge_on_synthetic_prices", "model_version": "demo-0.0.1",
        "training_date": datetime.now(UTC).isoformat(), "target": TARGET_NAME, "horizon": HORIZON,
        "feature_set": "market_only", "feature_columns": MARKET_FEATURES, "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "allows_missing_values": False,
        "date_ranges": {k: [iso(v[0]), iso(v[1])] for k, v in split.items()},
        "validation_strategy": "single chronological split on SYNTHETIC data (demo only)",
        "package_versions": {"scikit-learn": sklearn.__version__, "pandas": pd.__version__, "numpy": np.__version__},
        "artifact_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
        "warning": "Synthetic random-walk data. Metrics are meaningless and exist only to exercise the pipeline.",
    }
    (out / "model_metadata.json").write_text(json.dumps(meta, indent=2))
    (out / "feature_columns.json").write_text(json.dumps(MARKET_FEATURES, indent=2))
    metrics = {"demo": True, "test": {"model": m, "baseline_zero_return": b, **baseline_improvement(m, b)}}
    (out / "evaluation_metrics.json").write_text(json.dumps(metrics, indent=2))
    return meta
