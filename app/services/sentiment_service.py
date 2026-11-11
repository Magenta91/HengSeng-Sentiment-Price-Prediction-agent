"""Optional FinBERT sentiment scorer (complements the LLM extraction)."""
from __future__ import annotations

import asyncio
from typing import Protocol


class SentimentScorer(Protocol):
    model_name: str

    async def score(self, texts: list[str]) -> list[float]: ...


class FinBertScorer:
    """Scores text in [-1, 1] as P(positive) - P(negative) using ProsusAI/finbert.

    English-centric: not suitable for Chinese/Korean posts. Requires ``pip install transformers torch``.
    Loaded lazily; the pipeline marks the SentimentWorker as skipped when not enabled.
    """

    def __init__(self, model_id: str = "ProsusAI/finbert", batch_size: int = 16):
        self.model_name, self.batch_size, self._pipe = f"finbert:{model_id}", batch_size, None
        self._model_id = model_id

    def _load(self):
        if self._pipe is None:
            from transformers import pipeline
            self._pipe = pipeline("text-classification", model=self._model_id, top_k=None, truncation=True, max_length=512)
        return self._pipe

    def _score_sync(self, texts: list[str]) -> list[float]:
        pipe, out = self._load(), []
        for i in range(0, len(texts), self.batch_size):
            for scores in pipe(texts[i:i + self.batch_size]):
                by = {s["label"].lower(): s["score"] for s in scores}
                out.append(float(by.get("positive", 0.0) - by.get("negative", 0.0)))
        return out

    async def score(self, texts: list[str]) -> list[float]:
        return await asyncio.to_thread(self._score_sync, texts)
