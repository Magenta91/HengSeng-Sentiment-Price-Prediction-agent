"""Environment-driven settings (secrets only ever come from env / .env, never from code)."""
from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", protected_namespaces=("settings_",))

    app_env: str = "development"
    log_level: str = "INFO"
    storage_backend: Literal["memory", "postgres"] = "memory"
    database_url: str = "postgresql+psycopg://user:password@localhost:5432/sentiment_agent"

    apify_api_token: str = ""
    exa_api_key: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash-exp"
    finbert_enabled: bool = False

    market_data_provider: Literal["csv", "yfinance"] = "yfinance"
    market_data_csv_dir: str = "data/market"

    model_path: str = "model_artifacts/xgboost_model.joblib"
    model_metadata_path: str = "model_artifacts/model_metadata.json"
    demo_model_dir: str = "model_artifacts/demo"
    exchanges_config_path: str = "config/exchanges.json"

    default_lookback_days: int = Field(10, ge=1, le=60)
    market_history_days: int = Field(400, ge=60, le=3000)
    cors_origins: str = "http://localhost:5173"

    @property
    def secrets(self) -> list[str]:
        return [self.apify_api_token, self.exa_api_key, self.gemini_api_key]
