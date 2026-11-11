"""Comprehensive model and prediction pipeline verification.

Verifies:
1. Model artifact loading
2. Feature schema compatibility
3. Prediction logic
4. Market data integration
5. Feature engineering
6. End-to-end prediction workflow
"""
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Load environment variables
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parents[1] / ".env"
    load_dotenv(env_path)
except ImportError:
    print("⚠ WARNING: python-dotenv not installed, using system environment only")

import numpy as np
import pandas as pd

from app.core.config_models import ExchangesConfig
from app.integrations.market_data_client import build_provider
from app.ml.feature_schema import ALL_FEATURES, MARKET_FEATURES, build_feature_frame
from app.repositories.memory import MemoryRepository
from app.services.model_service import ModelService
from app.workers.prediction import (
    FeatureEngineeringWorker,
    FeaturePayload,
    PredictionWorker,
)


async def main():
    print("=" * 80)
    print("MODEL AND PREDICTION PIPELINE VERIFICATION")
    print("=" * 80)
    print()
    
    # PHASE 1: MODEL ARTIFACT DISCOVERY
    print("=" * 80)
    print("PHASE 1: MODEL ARTIFACT DISCOVERY")
    print("=" * 80)
    print()
    
    model_path = Path("model_artifacts/random_forest_model.joblib")
    metadata_path = Path("model_artifacts/model_metadata.json")
    columns_path = Path("model_artifacts/feature_columns.json")
    metrics_path = Path("model_artifacts/evaluation_metrics.json")
    
    print("Checking model artifacts...")
    artifacts = {
        "Model file": model_path,
        "Metadata file": metadata_path,
        "Feature columns": columns_path,
        "Evaluation metrics": metrics_path,
    }
    
    for name, path in artifacts.items():
        status = "✓" if path.exists() else "✗"
        print(f"  {status} {name}: {path}")
    
    print()
    
    # PHASE 2: MODEL LOADING
    print("=" * 80)
    print("PHASE 2: MODEL LOADING")
    print("=" * 80)
    print()
    
    print("Loading model...")
    try:
        model_service = ModelService.load(model_path, metadata_path)
        
        if not model_service.available:
            print(f"❌ Model unavailable: {model_service.unavailable_reason}")
            return
        
        print("✓ Model loaded successfully")
        print()
        
        info = model_service.info()
        print("Model Information:")
        print(f"  Model name: {info['model_name']}")
        print(f"  Model version: {info['model_version']}")
        print(f"  Feature set: {info['feature_set']}")
        print(f"  Feature schema version: {info['feature_schema_version']}")
        print(f"  Trained at: {info['trained_at']}")
        print(f"  Target: {info['target']}")
        print(f"  Horizon: {info['horizon']}")
        print(f"  Validation strategy: {info['validation_strategy']}")
        print()
        
        print("Training/Test Date Ranges:")
        for split, dates in info['date_ranges'].items():
            print(f"  {split}: {dates[0]} to {dates[1]}")
        print()
        
        print(f"Feature columns ({len(model_service.feature_columns)}):")
        for i, col in enumerate(model_service.feature_columns, 1):
            print(f"  {i:2d}. {col}")
        print()
        
        print("Model allows missing values:", model_service.metadata.get("allows_missing_values", False))
        print()
        
    except Exception as e:
        print(f"❌ Error loading model: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # PHASE 3: FEATURE SCHEMA COMPATIBILITY
    print("=" * 80)
    print("PHASE 3: FEATURE SCHEMA COMPATIBILITY")
    print("=" * 80)
    print()
    
    print(f"Model expects {len(model_service.feature_columns)} features")
    print(f"Schema defines {len(ALL_FEATURES)} total features")
    print(f"  - Market features: {len(MARKET_FEATURES)}")
    print()
    
    # Check if all model features are in schema
    unknown_features = [f for f in model_service.feature_columns if f not in ALL_FEATURES]
    if unknown_features:
        print(f"❌ Model uses features not in schema: {unknown_features}")
        return
    else:
        print("✓ All model features are defined in schema")
    
    # Check if model uses only market features
    uses_sentiment = any(f for f in model_service.feature_columns if f not in MARKET_FEATURES)
    if uses_sentiment:
        print("⚠ Model uses sentiment features (requires social/news data)")
        print(f"  Sentiment features: {[f for f in model_service.feature_columns if f not in MARKET_FEATURES]}")
    else:
        print("✓ Model uses market features only (no sentiment required)")
    
    print()
    
    # PHASE 4: SYNTHETIC DATA PREDICTION TEST
    print("=" * 80)
    print("PHASE 4: SYNTHETIC DATA PREDICTION TEST")
    print("=" * 80)
    print()
    
    print("Generating synthetic market data...")
    rng = np.random.default_rng(42)
    n_bars = 100
    base_price = 100.0
    
    # Generate realistic price series with random walk
    returns = rng.normal(0, 0.01, n_bars)
    prices = base_price * np.exp(np.cumsum(returns))
    
    synthetic_bars = pd.DataFrame({
        "session_date": pd.bdate_range("2026-01-01", periods=n_bars),
        "open": prices * (1 + rng.normal(0, 0.001, n_bars)),
        "high": prices * (1 + np.abs(rng.normal(0.005, 0.005, n_bars))),
        "low": prices * (1 - np.abs(rng.normal(0.005, 0.005, n_bars))),
        "close": prices,
        "volume": rng.integers(1_000_000, 10_000_000, n_bars),
    })
    
    print(f"  Generated {n_bars} bars")
    print(f"  Price range: {prices.min():.2f} to {prices.max():.2f}")
    print()
    
    print("Building features...")
    try:
        feature_frame = build_feature_frame(synthetic_bars)
        print(f"  Feature frame shape: {feature_frame.shape}")
        print(f"  Features: {list(feature_frame.columns[:5])}... (showing first 5)")
        print()
        
        # Get last row for prediction
        latest = feature_frame.iloc[[-1]]
        print("Latest feature values (sample):")
        for feat in model_service.feature_columns[:5]:
            val = latest[feat].values[0]
            print(f"  {feat}: {val:.6f}")
        print(f"  ... ({len(model_service.feature_columns) - 5} more features)")
        print()
        
        # Make prediction
        print("Making prediction...")
        pred_return = model_service.predict(latest)[0]
        current_price = latest["close"].values[0]
        pred_price = current_price * (1 + pred_return)
        
        print(f"  Current price: ${current_price:.2f}")
        print(f"  Predicted return: {pred_return:+.4f} ({pred_return*100:+.2f}%)")
        print(f"  Predicted price: ${pred_price:.2f}")
        print()
        
        print("✓ Synthetic data prediction successful")
        print()
        
    except Exception as e:
        print(f"❌ Error in synthetic prediction: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # PHASE 5: LIVE MARKET DATA PREDICTION
    print("=" * 80)
    print("PHASE 5: LIVE MARKET DATA PREDICTION")
    print("=" * 80)
    print()
    
    # Load exchanges config
    config_path = Path("config/exchanges.json")
    exchanges = ExchangesConfig.load(config_path)
    
    hkex = exchanges.get("HKEX")
    krx = exchanges.get("KRX")
    
    # Test with a few tickers
    test_symbols = ["0700.HK", "9988.HK", "005930.KS", "000660.KS"]
    test_assets = [a for a in list(hkex.assets) + list(krx.assets) if a.symbol in test_symbols]
    
    print(f"Testing with {len(test_assets)} tickers:")
    for asset in test_assets:
        print(f"  - {asset.symbol}: {asset.name}")
    print()
    
    # Fetch live market data
    print("Fetching live market data...")
    provider = build_provider("yfinance", None)
    
    end_date = datetime.now(UTC).date()
    start_date = end_date - timedelta(days=400)  # Sufficient history for indicators
    
    bars_by_symbol = {}
    for asset in test_assets:
        try:
            bars = await provider.get_daily_bars(asset.symbol, start_date, end_date, hkex.calendar())
            if bars:
                print(f"  ✓ {asset.symbol}: {len(bars)} bars")
                bars_by_symbol[asset.symbol] = bars
            else:
                print(f"  ✗ {asset.symbol}: No data")
        except Exception as e:
            print(f"  ✗ {asset.symbol}: {str(e)[:50]}")
    
    print()
    
    if not bars_by_symbol:
        print("⚠ No market data available, skipping live prediction test")
        return
    
    # PHASE 6: FEATURE ENGINEERING WORKER
    print("=" * 80)
    print("PHASE 6: FEATURE ENGINEERING WORKER")
    print("=" * 80)
    print()
    
    repo = MemoryRepository()
    feature_worker = FeatureEngineeringWorker(repo)
    
    now = datetime.now(UTC)
    payload = FeaturePayload(
        bars_by_symbol=bars_by_symbol,
        rows=None,  # No sentiment data
        calendar=hkex.calendar(),
        now=now
    )
    
    print("Running feature engineering...")
    try:
        result = await feature_worker.execute(payload, {"run_id": "model_test"})
        feature_result = result.output
        
        print(f"  Features generated: {len(feature_result.rows)}")
        print(f"  Symbols skipped: {len(feature_result.skipped)}")
        
        if feature_result.skipped:
            print("\n  Skipped symbols:")
            for sym, reason in feature_result.skipped.items():
                print(f"    {sym}: {reason}")
        
        print()
        
        if not feature_result.rows:
            print("⚠ No features generated, cannot proceed to prediction")
            return
        
        # Show sample features
        sample = feature_result.rows[0]
        print(f"Sample features for {sample.symbol}:")
        print(f"  As of date: {sample.as_of_date}")
        print(f"  Current price: ${sample.close:.2f}")
        print(f"  Coverage:")
        print(f"    Social posts: {sample.coverage['social_posts']}")
        print(f"    News articles: {sample.coverage['news_articles']}")
        print(f"    Has social: {sample.coverage['has_social']}")
        print(f"    Has news: {sample.coverage['has_news']}")
        print()
        
        print("  Feature values (first 5):")
        for i, (feat, val) in enumerate(list(sample.features.items())[:5]):
            print(f"    {feat}: {val:.6f}")
        print(f"    ... ({len(sample.features) - 5} more features)")
        print()
        
    except Exception as e:
        print(f"❌ Error in feature engineering: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # PHASE 7: PREDICTION WORKER
    print("=" * 80)
    print("PHASE 7: PREDICTION WORKER")
    print("=" * 80)
    print()
    
    prediction_worker = PredictionWorker(model_service, repo, hkex.calendar())
    
    print("Running predictions...")
    try:
        pred_result = await prediction_worker.execute(feature_result.rows, {"run_id": "model_test"})
        predictions = pred_result.output
        
        print(f"  Predictions made: {len(predictions.predictions)}")
        print(f"  Symbols skipped: {len(predictions.skipped)}")
        
        if predictions.skipped:
            print("\n  Skipped symbols:")
            for sym, reason in predictions.skipped.items():
                print(f"    {sym}: {reason}")
        
        print()
        
        if not predictions.predictions:
            print("⚠ No predictions made")
            return
        
        # Show all predictions
        print("PREDICTIONS:")
        print("-" * 80)
        for pred in predictions.predictions:
            asset = next((a for a in test_assets if a.symbol == pred.symbol), None)
            name = asset.name if asset else "Unknown"
            
            direction = "↑" if pred.predicted_return > 0 else "↓"
            magnitude = abs(pred.predicted_return * 100)
            
            print(f"\n{pred.symbol} ({name})")
            print(f"  As of date: {pred.as_of_date}")
            print(f"  Target date: {pred.target_session_date}")
            print(f"  Current price: ${pred.current_price:.2f}")
            print(f"  Predicted return: {pred.predicted_return:+.4f} ({pred.predicted_return*100:+.2f}%)")
            print(f"  Predicted price: ${pred.predicted_price:.2f} {direction}")
            print(f"  Model: {pred.model_name} v{pred.model_version}")
        
        print("\n" + "-" * 80)
        print()
        
        # Statistics
        returns = [p.predicted_return for p in predictions.predictions]
        print("Prediction Statistics:")
        print(f"  Mean return: {np.mean(returns):+.4f} ({np.mean(returns)*100:+.2f}%)")
        print(f"  Std dev: {np.std(returns):.4f}")
        print(f"  Min return: {min(returns):+.4f} ({min(returns)*100:+.2f}%)")
        print(f"  Max return: {max(returns):+.4f} ({max(returns)*100:+.2f}%)")
        print(f"  Positive predictions: {sum(1 for r in returns if r > 0)}/{len(returns)}")
        print(f"  Negative predictions: {sum(1 for r in returns if r < 0)}/{len(returns)}")
        print()
        
        print("✓ Prediction worker successful")
        print()
        
    except Exception as e:
        print(f"❌ Error in prediction: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # PHASE 8: PERSISTENCE VERIFICATION
    print("=" * 80)
    print("PHASE 8: PERSISTENCE VERIFICATION")
    print("=" * 80)
    print()
    
    # Check feature snapshots
    features_stored = await repo.select("feature_snapshots", where={})
    print(f"Feature snapshots stored: {len(features_stored)}")
    
    if features_stored:
        sample = features_stored[0]
        print(f"  Sample feature snapshot:")
        print(f"    Symbol: {sample.get('asset_symbol')}")
        print(f"    As of date: {sample.get('as_of_date')}")
        print(f"    Schema version: {sample.get('schema_version')}")
        print(f"    Features count: {len(sample.get('features', {}))}")
    print()
    
    # Check predictions
    preds_stored = await repo.select("predictions", where={})
    print(f"Predictions stored: {len(preds_stored)}")
    
    if preds_stored:
        sample = preds_stored[0]
        print(f"  Sample prediction:")
        print(f"    Symbol: {sample.get('symbol')}")
        print(f"    As of date: {sample.get('as_of_date')}")
        print(f"    Predicted return: {sample.get('predicted_return'):+.4f}")
        print(f"    Model: {sample.get('model_name')} v{sample.get('model_version')}")
    print()
    
    print("✓ Persistence verified")
    print()
    
    # FINAL SUMMARY
    print("=" * 80)
    print("VERIFICATION SUMMARY")
    print("=" * 80)
    print()
    
    checks = []
    checks.append(("Model artifact found", model_path.exists()))
    checks.append(("Metadata found", metadata_path.exists()))
    checks.append(("Model loads successfully", model_service.available))
    checks.append(("Feature schema compatible", len(unknown_features) == 0))
    checks.append(("Synthetic prediction works", True))  # If we got here
    checks.append(("Live market data fetched", len(bars_by_symbol) > 0))
    checks.append(("Features engineered", len(feature_result.rows) > 0))
    checks.append(("Predictions generated", len(predictions.predictions) > 0))
    checks.append(("Features persisted", len(features_stored) > 0))
    checks.append(("Predictions persisted", len(preds_stored) > 0))
    
    for check, passed in checks:
        status = "✓" if passed else "✗"
        print(f"  {status} {check}")
    print()
    
    all_passed = all(passed for _, passed in checks)
    if all_passed:
        print("✅ ALL VERIFICATION CHECKS PASSED")
        print()
        print("Model Information:")
        print(f"  Type: {info['model_name']}")
        print(f"  Target: {info['target']} ({info['horizon']})")
        print(f"  Feature set: {info['feature_set']}")
        print(f"  Test period: {info['date_ranges']['test'][0]} to {info['date_ranges']['test'][1]}")
        print(f"  Test MAE: {info['metrics']['test']['model']['mae']:.6f}")
        print(f"  Test RMSE: {info['metrics']['test']['model']['rmse']:.6f}")
        print(f"  Directional accuracy: {info['metrics']['test']['model']['directional_accuracy']:.2%}")
    else:
        print("⚠ SOME VERIFICATION CHECKS FAILED")
    
    print()


if __name__ == "__main__":
    asyncio.run(main())
