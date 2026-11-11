"""Loads a trusted, Colab-trained ``.joblib`` artifact once and validates it against the
feature schema. The application never trains."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from app.core.exceptions import ModelArtifactError
from app.ml.feature_schema import ALL_FEATURES, FEATURE_SCHEMA_VERSION, FeatureSchemaError, select_features

REQUIRED_METADATA = ("model_name", "model_version", "feature_schema_version", "feature_columns", "target", "horizon")


class ModelService:
    def __init__(self):
        self.model = None
        self.metadata: dict = {}
        self.metrics: dict = {}
        self.feature_columns: list[str] = []
        self.unavailable_reason: str | None = "model not loaded"

    @property
    def available(self) -> bool:
        return self.model is not None

    @classmethod
    def load(cls, model_path: str | Path, metadata_path: str | Path, *, columns_path: str | Path | None = None,
             metrics_path: str | Path | None = None) -> ModelService:
        """Load + validate. Never raises for a *missing* artifact (pipeline degrades gracefully and says
        why); raises ModelArtifactError for artifacts that exist but are inconsistent or untrusted."""
        svc = cls()
        model_path, metadata_path = Path(model_path), Path(metadata_path)
        if not model_path.exists() or not metadata_path.exists():
            svc.unavailable_reason = f"artifact not found ({model_path} / {metadata_path}); see model_artifacts/README.md"
            return svc
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        missing = [k for k in REQUIRED_METADATA if k not in metadata]
        if missing:
            raise ModelArtifactError(f"model metadata missing keys: {missing}")
        if metadata["feature_schema_version"] != FEATURE_SCHEMA_VERSION:
            raise ModelArtifactError(
                f"feature schema mismatch: artifact {metadata['feature_schema_version']} vs app {FEATURE_SCHEMA_VERSION}")
        columns = list(metadata["feature_columns"])
        unknown = [c for c in columns if c not in ALL_FEATURES]
        if unknown:
            raise ModelArtifactError(f"artifact uses features unknown to the schema: {unknown}")
        columns_path = Path(columns_path) if columns_path else metadata_path.with_name("feature_columns.json")
        if columns_path.exists() and json.loads(columns_path.read_text(encoding="utf-8")) != columns:
            raise ModelArtifactError("feature_columns.json does not match model_metadata.json feature_columns")
        expected_hash = metadata.get("artifact_sha256")
        if expected_hash and hashlib.sha256(model_path.read_bytes()).hexdigest() != expected_hash:
            raise ModelArtifactError("model file hash does not match metadata (refusing to load an altered artifact)")
        import joblib  # joblib files are pickles: only load artifacts you produced yourself
        model = joblib.load(model_path)
        if not hasattr(model, "predict"):
            raise ModelArtifactError("loaded object has no predict()")
        n_in = getattr(model, "n_features_in_", None)
        if n_in is not None and n_in != len(columns):
            raise ModelArtifactError(f"model expects {n_in} features, metadata lists {len(columns)}")
        names_in = getattr(model, "feature_names_in_", None)
        if names_in is not None and list(names_in) != columns:
            raise ModelArtifactError("model feature order differs from metadata feature_columns")
        metrics_path = Path(metrics_path) if metrics_path else metadata_path.with_name("evaluation_metrics.json")
        svc.model, svc.metadata, svc.feature_columns = model, metadata, columns
        svc.metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
        svc.unavailable_reason = None
        return svc

    def predict(self, features: pd.DataFrame) -> np.ndarray:
        """Predict next-session returns for rows of a feature frame (columns reordered to the schema)."""
        if not self.available:
            raise ModelArtifactError(self.unavailable_reason or "model unavailable")
        x = select_features(features, self.feature_columns)
        if not self.metadata.get("allows_missing_values", False) and x.isna().any().any():
            bad = x.columns[x.isna().any()].tolist()
            raise FeatureSchemaError(f"missing values in {bad}; this model does not accept NaN")
        return np.asarray(self.model.predict(x), dtype=float)

    def info(self) -> dict:
        if not self.available:
            return {"available": False, "reason": self.unavailable_reason}
        m = self.metadata
        return {"available": True, "model_name": m["model_name"], "model_version": m["model_version"],
                "feature_set": m.get("feature_set"), "feature_schema_version": m["feature_schema_version"],
                "trained_at": m.get("training_date"), "target": m["target"], "horizon": m["horizon"],
                "date_ranges": m.get("date_ranges"), "demo": bool(m.get("demo", False)),
                "metrics": self.metrics, "validation_strategy": m.get("validation_strategy")}
