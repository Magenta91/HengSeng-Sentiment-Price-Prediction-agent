import asyncio
import json
from pathlib import Path

from app.core.exceptions import ModelArtifactError
from app.ml.feature_schema import MARKET_FEATURES, FeatureSchemaError, build_feature_frame
from app.services.model_service import ModelService
from tests.helpers import demo_model
from tests.unit.test_feature_schema import bars


def build(tmp_path):
    asyncio.run(demo_model(str(tmp_path)))
    return Path(tmp_path)


def load(d):
    return ModelService.load(d / "xgboost_model.joblib", d / "model_metadata.json")


def test_loads_and_predicts_with_columns_reordered(tmp_path):
    svc = load(build(tmp_path))
    assert svc.available and svc.feature_columns == MARKET_FEATURES
    frame = build_feature_frame(bars()).tail(3)
    shuffled = frame[list(reversed(frame.columns))]
    assert (svc.predict(shuffled) == svc.predict(frame)).all()        # order of input columns is irrelevant
    assert svc.info()["demo"] is True


def test_missing_artifact_degrades_with_reason(tmp_path):
    svc = ModelService.load(tmp_path / "x.joblib", tmp_path / "m.json")
    assert not svc.available and "not found" in svc.unavailable_reason


def test_schema_version_mismatch_and_tampering_rejected(tmp_path):
    d = build(tmp_path)
    meta = json.loads((d / "model_metadata.json").read_text())
    (d / "model_metadata.json").write_text(json.dumps({**meta, "feature_schema_version": "0.0.1"}))
    for _ in range(1):
        try:
            load(d)
        except ModelArtifactError as e:
            assert "schema" in str(e)
        else:
            raise AssertionError
    (d / "model_metadata.json").write_text(json.dumps(meta))
    (d / "xgboost_model.joblib").write_bytes((d / "xgboost_model.joblib").read_bytes() + b"x")
    try:
        load(d)
    except ModelArtifactError as e:
        assert "hash" in str(e)
    else:
        raise AssertionError


def test_feature_column_mismatch_between_files_rejected(tmp_path):
    d = build(tmp_path)
    (d / "feature_columns.json").write_text(json.dumps(list(reversed(MARKET_FEATURES))))
    try:
        load(d)
    except ModelArtifactError as e:
        assert "feature_columns" in str(e)
    else:
        raise AssertionError


def test_nan_features_rejected_when_model_cannot_handle_them(tmp_path):
    svc = load(build(tmp_path))
    frame = build_feature_frame(bars()).tail(1).copy()
    frame["ret_1"] = float("nan")
    try:
        svc.predict(frame)
    except FeatureSchemaError:
        pass
    else:
        raise AssertionError
