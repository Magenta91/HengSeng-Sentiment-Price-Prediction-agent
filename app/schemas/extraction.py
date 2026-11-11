"""Pydantic schema for LLM structured output (validated; malformed output is rejected/retried)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PositionOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["long", "short"] | None = None
    quantity: float | None = None
    entry_price: float | None = None
    evidence_text: str | None = None


class ExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    asset_symbol: str
    asset_name: str | None = None
    mentioned: bool = True                      # False => the text does not actually discuss this asset
    sentiment: Literal["bullish", "bearish", "neutral"]
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    direction: Literal["up", "down", "sideways", "unclear"]
    position: PositionOut | None = None
    support_levels: list[float] = Field(default_factory=list)
    resistance_levels: list[float] = Field(default_factory=list)
    target_price: float | None = None
    confidence: float = Field(ge=0.0, le=1.0)   # extraction confidence, not a calibrated probability
    evidence_text: str | None = None

    @field_validator("support_levels", "resistance_levels", mode="before")
    @classmethod
    def _none_to_empty(cls, v):
        return [] if v is None else v
