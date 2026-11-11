from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential backoff. ``max_attempts`` counts the first try."""

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: float = 0.1

    def delay(self, attempt: int, retry_after: float | None = None, rng: Callable[[], float] = random.random) -> float:
        """Delay before attempt ``attempt + 1`` (``attempt`` is 1-based)."""
        raw = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        if retry_after is not None:
            raw = min(max(raw, retry_after), self.max_delay)
        return raw * (1 + self.jitter * rng())


async def retry_async(fn: Callable[[], Awaitable[T]], policy: RetryPolicy, *,
                      is_retryable: Callable[[BaseException], bool],
                      sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                      on_retry: Callable[[int, BaseException, float], None] | None = None) -> T:
    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn()
        except Exception as exc:
            if attempt >= policy.max_attempts or not is_retryable(exc):
                raise
            retry_after = getattr(exc, "retry_after", None)
            if retry_after is None and getattr(exc, "cause", None) is not None:
                retry_after = getattr(exc.cause, "retry_after", None)
            delay = policy.delay(attempt, retry_after)
            if on_retry:
                on_retry(attempt, exc, delay)
            await sleep(delay)
