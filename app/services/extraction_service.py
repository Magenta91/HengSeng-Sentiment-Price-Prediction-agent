"""LLM-assisted structured financial extraction with bounded retries and a grounding guard."""
from __future__ import annotations

import json
import re

from pydantic import ValidationError

from app.core.exceptions import ExtractionError
from app.integrations.llm_client import LLMClient
from app.schemas.extraction import ExtractionOutput
from app.schemas.records import Extraction
from app.services.grounding import apply_grounding
from app.services.ticker_relevance import apply_ticker_relevance_filter
from app.utils import utcnow

SYSTEM_PROMPT = """You extract structured financial information about ONE asset from a social post or news article.
Reply with a single JSON object and nothing else (no markdown, no commentary).

Rules:
- Only report what the text explicitly states. Never infer or guess.
- position: set type ("long"/"short") ONLY if the author states they hold or just opened that position, and copy the
  exact words into position.evidence_text. Otherwise position must be null.
- support_levels / resistance_levels / target_price: ONLY numbers written in the text for this asset. Otherwise [] / null.
- evidence_text and position.evidence_text must be copied VERBATIM from the text (<= 200 characters).
- If the text does not actually discuss the asset, set "mentioned": false.
- sentiment_score in [-1, 1] (bearish negative); confidence in [0, 1] expresses how clear the text is.

JSON keys: asset_symbol, asset_name, mentioned, sentiment (bullish|bearish|neutral), sentiment_score, direction
(up|down|sideways|unclear), position {type, quantity, entry_price, evidence_text} or null, support_levels [numbers],
resistance_levels [numbers], target_price number|null, confidence, evidence_text."""


def build_user_prompt(symbol: str, asset_name: str, text: str, source_type: str) -> str:
    return f"ASSET: {symbol} ({asset_name})\nSOURCE TYPE: {source_type}\nTEXT:\n{text[:4000]}"


def _parse_json(raw: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    return json.loads(cleaned[start:end + 1])


class ExtractionService:
    def __init__(self, llm: LLMClient, *, max_attempts: int = 3):
        self.llm, self.max_attempts = llm, max_attempts

    @property
    def model_name(self) -> str:
        return getattr(self.llm, "model", "unknown")

    async def extract(self, record, symbol: str, asset_name: str) -> Extraction | None:
        """Return a validated, grounded extraction; ``None`` if the asset is not discussed.
        Raises ExtractionError when output stays malformed after ``max_attempts``."""
        prompt = build_user_prompt(symbol, asset_name, record.text, record.source_type)
        last_error = ""
        for attempt in range(1, self.max_attempts + 1):
            user = prompt if not last_error else (
                f"{prompt}\n\nYour previous reply was invalid ({last_error}). Reply again with ONLY the JSON object.")
            raw = await self.llm.complete(SYSTEM_PROMPT, user)
            try:
                out = ExtractionOutput.model_validate(_parse_json(raw))
            except (ValueError, ValidationError) as exc:   # JSONDecodeError subclasses ValueError
                last_error = str(exc).splitlines()[0][:200]
                continue
            if not out.mentioned:
                return None
            
            # Apply grounding
            data, grounding_notes = apply_grounding(out.model_dump(), record.text)
            
            # Apply ticker relevance filter
            data, relevance_notes = apply_ticker_relevance_filter(data, record.text)
            if data is None:
                # Extraction filtered out as not relevant
                return None
            
            # Combine all notes
            notes = grounding_notes + relevance_notes
            
            return Extraction(
                record_id=record.record_id, source_type=record.source_type, source=record.source, symbol=symbol,
                asset_name=data.get("asset_name") or asset_name, sentiment=data["sentiment"],
                sentiment_score=data["sentiment_score"], direction=data["direction"], position=data.get("position"),
                support_levels=data["support_levels"], resistance_levels=data["resistance_levels"],
                target_price=data.get("target_price"), confidence=data["confidence"],
                evidence_text=data.get("evidence_text"), source_timestamp=record.event_time,
                model_name=self.model_name, extracted_at=utcnow(), guard_notes=notes)
        raise ExtractionError(f"{record.record_id}/{symbol}: invalid LLM output after {self.max_attempts} attempts: {last_error}")
