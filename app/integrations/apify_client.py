"""Apify REST client: run an actor, poll to completion, page through the dataset.

Written against Apify's public REST API v2 (POST /acts/{id}/runs, GET /actor-runs/{id},
GET /datasets/{id}/items). It has NOT been exercised against the live service from this
repository's build environment - verify with your own token before relying on it.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.core.exceptions import IntegrationError
from app.core.retry import RetryPolicy
from app.integrations.http import HttpTransport, request_json

TERMINAL_OK = {"SUCCEEDED"}
TERMINAL_BAD = {"FAILED", "ABORTED", "TIMED-OUT"}


@dataclass
class ApifyResult:
    items: list[dict]
    actor_id: str
    run_id: str
    dataset_id: str
    status: str


class ApifyClient:
    def __init__(self, token: str, transport: HttpTransport, *, base_url: str = "https://api.apify.com/v2",
                 poll_interval: float = 5.0, run_timeout: float = 600.0, request_timeout: float = 30.0,
                 retry: RetryPolicy = RetryPolicy(), sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 clock: Callable[[], float] = time.monotonic):
        if not token:
            raise IntegrationError("APIFY_API_TOKEN is not configured")
        self._token = token
        self.transport, self.base_url = transport, base_url.rstrip("/")
        self.poll_interval, self.run_timeout, self.request_timeout = poll_interval, run_timeout, request_timeout
        self.retry, self.sleep, self.clock = retry, sleep, clock

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    async def _call(self, method: str, path: str, **kw) -> Any:
        return await request_json(self.transport, method, f"{self.base_url}{path}", headers=self._headers(),
                                  timeout=self.request_timeout, policy=self.retry, sleep=self.sleep, **kw)

    @staticmethod
    def _actor_path(actor_id: str) -> str:
        return actor_id.replace("/", "~")      # Apify's URL form of "username/actor-name"

    async def start_run(self, actor_id: str, run_input: dict) -> dict:
        body = await self._call("POST", f"/acts/{self._actor_path(actor_id)}/runs", json=run_input)
        return body["data"]

    async def wait_for_run(self, run_id: str) -> dict:
        deadline = self.clock() + self.run_timeout
        while True:
            data = (await self._call("GET", f"/actor-runs/{run_id}"))["data"]
            status = data.get("status")
            if status in TERMINAL_OK:
                return data
            if status in TERMINAL_BAD:
                raise IntegrationError(f"Apify run {run_id} ended with status {status}")
            if self.clock() >= deadline:
                # Not marked transient: blindly restarting a long actor run would double the spend.
                raise IntegrationError(f"Apify run {run_id} still {status} after {self.run_timeout:.0f}s")
            await self.sleep(self.poll_interval)

    async def fetch_dataset(self, dataset_id: str, *, max_items: int = 1000, page_size: int = 500) -> list[dict]:
        items: list[dict] = []
        offset = 0
        while len(items) < max_items:
            limit = min(page_size, max_items - len(items))
            page = await self._call("GET", f"/datasets/{dataset_id}/items",
                                    params={"format": "json", "clean": "true", "offset": offset, "limit": limit})
            if not isinstance(page, list):
                raise IntegrationError("unexpected dataset response shape")
            items.extend(page)
            if len(page) < limit:
                break
            offset += len(page)
        return items

    async def run_and_fetch(self, actor_id: str, run_input: dict, *, max_items: int = 1000) -> ApifyResult:
        started = await self.start_run(actor_id, run_input)
        finished = await self.wait_for_run(started["id"])
        dataset_id = finished.get("defaultDatasetId") or started.get("defaultDatasetId")
        if not dataset_id:
            raise IntegrationError(f"Apify run {started['id']} has no default dataset")
        items = await self.fetch_dataset(dataset_id, max_items=max_items)
        return ApifyResult(items, actor_id, started["id"], dataset_id, finished.get("status", ""))
