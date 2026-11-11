"""Minimal async HTTP abstraction.

Clients depend on the ``HttpTransport`` protocol, not on httpx, so tests inject a fake
transport and no network is ever needed. ``request_json`` adds timeouts mapping, status
classification (429/5xx -> TransientError) and bounded retry with exponential backoff.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.exceptions import IntegrationError, TransientError
from app.core.retry import RetryPolicy, retry_async


@dataclass
class HttpResponse:
    status: int
    body: Any = None
    headers: dict = field(default_factory=dict)


class HttpTransport(Protocol):
    async def request(self, method: str, url: str, *, headers: dict | None = None, params: dict | None = None,
                      json: Any = None, timeout: float = 30.0) -> HttpResponse: ...


class HttpxTransport:
    """Production transport backed by httpx (imported lazily)."""

    def __init__(self):
        self._client = None

    async def request(self, method, url, *, headers=None, params=None, json=None, timeout=30.0) -> HttpResponse:
        import httpx

        if self._client is None:
            self._client = httpx.AsyncClient()
        try:
            r = await self._client.request(method, url, headers=headers, params=params, json=json, timeout=timeout)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TransientError(f"network error calling {_host(url)}: {type(exc).__name__}") from exc
        try:
            body = r.json()
        except ValueError:
            body = r.text
        return HttpResponse(r.status_code, body, dict(r.headers))

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()


def _host(url: str) -> str:
    return url.split("/")[2] if "//" in url else url


def _retry_after(headers: dict) -> float | None:
    for k, v in headers.items():
        if k.lower() == "retry-after":
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


async def request_json(transport: HttpTransport, method: str, url: str, *, headers: dict | None = None,
                       params: dict | None = None, json: Any = None, timeout: float = 30.0,
                       policy: RetryPolicy = RetryPolicy(),
                       sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> Any:
    """Call an endpoint and return the decoded body.

    Transient: timeouts/transport errors, HTTP 408/425/429/5xx. Everything else >= 400 is
    raised as ``IntegrationError`` immediately (retrying a 401/404 cannot help). Response
    bodies are NOT echoed in errors, to avoid leaking credentials echoed by a service.
    """

    async def once():
        resp = await transport.request(method, url, headers=headers, params=params, json=json, timeout=timeout)
        if resp.status in (408, 425, 429) or resp.status >= 500:
            raise TransientError(f"{_host(url)} returned HTTP {resp.status}", _retry_after(resp.headers))
        if resp.status >= 400:
            raise IntegrationError(f"{_host(url)} returned HTTP {resp.status}")
        return resp.body

    return await retry_async(once, policy, is_retryable=lambda e: isinstance(e, TransientError), sleep=sleep)
