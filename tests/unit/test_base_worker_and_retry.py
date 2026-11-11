import asyncio

from app.core.base_worker import BaseWorker
from app.core.exceptions import TransientError, WorkerExecutionError
from app.core.retry import RetryPolicy, retry_async


class Ok(BaseWorker):
    async def run(self, payload, context):
        self.metric("n", len(payload))
        return payload[::-1]


class Boom(BaseWorker):
    async def run(self, payload, context):
        raise TransientError("rate limited", retry_after=3)


def test_lifecycle_returns_output_duration_and_metrics():
    res = asyncio.run(Ok("ok").execute([1, 2, 3], {"run_id": "r"}))
    assert res.output == [3, 2, 1] and res.metrics == {"n": 3} and res.duration_s >= 0


def test_none_payload_is_rejected_and_wrapped():
    try:
        asyncio.run(Ok("ok").execute(None))
    except WorkerExecutionError as e:
        assert not e.transient
    else:
        raise AssertionError


def test_failure_is_wrapped_with_cause_and_transient_flag():
    try:
        asyncio.run(Boom("boom").execute([1]))
    except WorkerExecutionError as e:
        assert e.transient and isinstance(e.cause, TransientError)
    else:
        raise AssertionError


def test_retry_bounded_exponential_backoff():
    calls, sleeps = [], []

    async def fn():
        calls.append(1)
        raise TransientError("x")

    async def sleep(d):
        sleeps.append(d)

    p = RetryPolicy(max_attempts=3, base_delay=1.0, max_delay=30, jitter=0.0)
    try:
        asyncio.run(retry_async(fn, p, is_retryable=lambda e: isinstance(e, TransientError), sleep=sleep))
    except TransientError:
        pass
    assert len(calls) == 3 and sleeps == [1.0, 2.0]


def test_non_retryable_error_is_not_retried():
    calls = []

    async def fn():
        calls.append(1)
        raise ValueError("bad")

    try:
        asyncio.run(retry_async(fn, RetryPolicy(), is_retryable=lambda e: False))
    except ValueError:
        pass
    assert len(calls) == 1
