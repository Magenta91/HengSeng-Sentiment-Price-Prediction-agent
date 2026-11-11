import asyncio
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pydantic_settings")

from fastapi.testclient import TestClient

from app.bootstrap import Container, build_demo_dependencies
from app.config import Settings
from app.core.orchestrator import PipelineConfig, PipelineOrchestrator
from app.main import create_app
from app.repositories.memory import MemoryRepository
from tests.helpers import NOW, demo_model, exchanges


def client():
    model = asyncio.run(demo_model())
    repo, ex = MemoryRepository(), exchanges()
    deps = build_demo_dependencies(repo, ex, model)
    deps.clock = lambda: NOW
    orch = PipelineOrchestrator(deps, PipelineConfig(worker_base_delay=0))
    return TestClient(create_app(Container(Settings(), repo, ex, orch, orch, model)))


def test_health_assets_and_unknown_routes():
    with client() as c:
        assert c.get("/health").json()["model_loaded"] is True
        data = c.get("/api/assets").json()
        assert {e["code"] for e in data} == {"HKEX", "KRX"} and len(data[0]["assets"]) == 5
        # LIHKG and Xueqiu are operational for HKEX; Naver is operational for KRX
        for e in data:
            for s in e["sources"]:
                if s["name"] in ("lihkg", "xueqiu") and e["code"] == "HKEX":
                    assert s["operational"] is True and s["problem"] is None
                elif s["name"] == "naver_stock_discussion" and e["code"] == "KRX":
                    assert s["operational"] is True and s["problem"] is None
                else:
                    assert s["operational"] is False and s["problem"]
        assert c.get("/api/assets/NOPE/intelligence").status_code == 404
        assert c.get("/api/reports/latest?exchange=HKEX").status_code == 404
        assert c.get("/api/pipeline/runs/nope").status_code == 404
        assert c.post("/api/pipeline/runs", json={"exchange": "NYSE", "mode": "demo"}).status_code == 404
        assert c.post("/api/pipeline/runs", json={"exchange": "HKEX", "lookback_days": 0}).status_code == 422


def test_run_lifecycle_report_predictions_and_intelligence():
    with client() as c:
        r = c.post("/api/pipeline/runs", json={"exchange": "HKEX", "mode": "demo"})
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        for _ in range(100):
            st = c.get(f"/api/pipeline/runs/{run_id}").json()
            if st["status"] in ("succeeded", "partial", "failed"):
                break
            time.sleep(0.2)
        assert st["status"] == "succeeded" and any(w["worker_name"] == "market_data" for w in st["workers"])
        rep = c.get("/api/reports/latest?exchange=HKEX").json()
        assert rep["is_demo"] and len(rep["assets"]) == 5
        preds = c.get("/api/predictions?exchange=HKEX").json()
        assert len(preds["predictions"]) == 5 and "not financial advice" in preds["disclaimer"]
        intel = c.get("/api/assets/0700.HK/intelligence").json()
        assert intel["extractions"] and intel["news"]
