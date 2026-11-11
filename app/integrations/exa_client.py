"""Exa search client for recent asset news (POST https://api.exa.ai/search).

Request/response shape written from Exa's documented API; not exercised live from this
build environment. Publication time (``publishedDate``) is kept distinct from retrieval time.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime

from app.core.exceptions import IntegrationError
from app.core.retry import RetryPolicy
from app.integrations.http import HttpTransport, request_json


class ExaClient:
    def __init__(self, api_key: str, transport: HttpTransport, *, base_url: str = "https://api.exa.ai",
                 category: str | None = "news", max_chars: int = 3000, timeout: float = 30.0,
                 retry: RetryPolicy = RetryPolicy(), sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        if not api_key:
            raise IntegrationError("EXA_API_KEY is not configured")
        self._key = api_key
        self.transport, self.base_url, self.category = transport, base_url.rstrip("/"), category
        self.max_chars, self.timeout, self.retry, self.sleep = max_chars, timeout, retry, sleep

    async def search_news(self, query: str, *, start: datetime, end: datetime, num_results: int = 10) -> list[dict]:
        body = {
            "query": query, "numResults": num_results, "type": "auto",
            "startPublishedDate": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "endPublishedDate": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "contents": {"text": {"maxCharacters": self.max_chars}},
        }
        if self.category:
            body["category"] = self.category
        data = await request_json(self.transport, "POST", f"{self.base_url}/search",
                                  headers={"x-api-key": self._key, "Content-Type": "application/json"},
                                  json=body, timeout=self.timeout, policy=self.retry, sleep=self.sleep)
        results = data.get("results") if isinstance(data, dict) else None
        if results is None:
            raise IntegrationError("unexpected Exa response shape")
        return results
