# Model artifacts

Produced by `notebooks/model_training.ipynb` in Google Colab and copied here. **No trained model is committed**
(and none is faked): until you add one, the pipeline runs without predictions and the report says why.

| File | Purpose |
|---|---|
| `xgboost_model.joblib` (or `<name>_model.joblib` + `MODEL_PATH`) | sklearn-compatible estimator / `Pipeline` |
| `feature_columns.json` | ordered feature names the model expects |
| `model_metadata.json` | name, version, target, horizon, feature set, schema version, date ranges, validation strategy, package versions, `artifact_sha256` |
| `evaluation_metrics.json` | walk-forward and held-out test metrics vs the zero-return baseline |

`ModelService` refuses to load an artifact if: required metadata keys are missing, the feature-schema version differs from
`app/ml/feature_schema.py`, `feature_columns.json` disagrees with the metadata, the file hash does not match `artifact_sha256`,
or the model's own `feature_names_in_` order differs. Verify with `python -m scripts.verify_model model_artifacts`.

**Security:** `.joblib` files are pickles and can execute code on load. Only load artifacts you trained yourself.

`model_artifacts/demo/` (git-ignored) is created by `python -m scripts.make_demo_artifacts` from **synthetic** prices so the demo mode works offline.
