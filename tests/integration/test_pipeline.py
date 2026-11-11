import asyncio
import json
from datetime import timedelta

from app.core.exceptions import IntegrationError, TransientError
from app.demo.fixtures import DemoMarketProvider, DemoSocialAdapter
from app.services.model_service import ModelService
from tests.helpers import NOW, demo_model, make_deps, orchestrator

HKEX = "HKEX"


class FlakyAdapter:
    """Fails with the given error for the first ``fail_times`` calls, then behaves like a demo source."""

    def __init__(self, name, error, fail_times=99):
        self.source_name, self.error, self.left = name, error, fail_times
        self.inner, self.calls = DemoSocialAdapter(name), 0

    async def fetch(self, request):
        self.calls += 1
        if self.left > 0:
            self.left -= 1
            raise self.error
        return await self.inner.fetch(request)


def run(coro):
    return asyncio.run(coro)


def by_name(summary):
    return {o.name: o for o in summary.outcomes}


def test_full_demo_run_produces_report_predictions_and_persistence():
    async def go():
        deps = make_deps(await demo_model())
        s = await orchestrator(deps).run(HKEX)
        return deps, s
    deps, s = run(go())
    assert s.status == "succeeded", [(o.name, o.status, o.error) for o in s.outcomes if o.status != "succeeded"]
    r = s.report
    assert r["is_demo"] and len(r["assets"]) == 5 and "not financial advice" in r["disclaimer"]
    assert all(a["prediction"] and a["price"]["current"] for a in r["assets"])
    assert r["rankings"][0]["mention_rank"] == 1 and len(r["rankings"]) == 5
    json.dumps(r)                                                       # report is JSON-serialisable (no NaN objects)
    tables = deps.repo._tables
    for t in ("social_posts", "news_articles", "market_bars", "predictions", "feature_snapshots",
              "sentiment_aggregates", "worker_runs", "pipeline_runs"):
        assert tables[t], t
    run_row = run_sync(deps.repo.select("pipeline_runs"))[0]
    assert run_row["status"] == "succeeded" and run_row["summary"]["report"]["assets"]


def run_sync(c):
    return asyncio.run(c)


def test_one_failed_source_does_not_discard_the_others_and_transient_is_retried_once():
    async def go():
        bad = FlakyAdapter("xueqiu", TransientError("429"))
        deps = make_deps(await demo_model(), social_adapters={"lihkg": DemoSocialAdapter("lihkg"), "xueqiu": bad})
        return deps, bad, await orchestrator(deps).run(HKEX)
    deps, bad, s = run(go())
    o = by_name(s)
    assert s.status == "partial" and o["social:xueqiu"].status == "failed" and o["social:xueqiu"].attempts == 2 and bad.calls == 2
    assert o["social:lihkg"].status == "succeeded" and o["market_data"].status == "succeeded"
    assert {p["source"] for p in deps.repo._tables["social_posts"].values()} == {"lihkg"}
    assert any("only 1 social source" in w for w in s.warnings)
    assert s.report["assets"][0]["coverage"]["social_posts"] > 0         # report still built from surviving data


def test_transient_failure_that_recovers_is_recorded_as_success_with_attempts():
    async def go():
        flaky = FlakyAdapter("xueqiu", TransientError("503"), fail_times=1)
        deps = make_deps(await demo_model(), social_adapters={"lihkg": DemoSocialAdapter("lihkg"), "xueqiu": flaky})
        return await orchestrator(deps).run(HKEX)
    s = run(go())
    assert s.status == "succeeded" and by_name(s)["social:xueqiu"].attempts == 2


def test_non_transient_error_is_not_retried():
    async def go():
        bad = FlakyAdapter("xueqiu", IntegrationError("401"))
        deps = make_deps(await demo_model(), social_adapters={"lihkg": DemoSocialAdapter("lihkg"), "xueqiu": bad})
        return bad, await orchestrator(deps).run(HKEX)
    bad, s = run(go())
    assert bad.calls == 1 and by_name(s)["social:xueqiu"].attempts == 1


def test_unconfigured_sources_are_skipped_and_visible_not_silently_ignored():
    async def go():
        deps = make_deps(await demo_model(), social_adapters={}, news_client=None,
                         skipped_sources={"lihkg": "no actor_id", "xueqiu": "no actor_id"})
        return await orchestrator(deps).run(HKEX)
    s = run(go())
    o = by_name(s)
    assert o["social:lihkg"].status == "skipped" and "actor_id" in o["social:lihkg"].reason
    assert o["news:exa"].status == "skipped" and s.status == "partial"
    assert any("only 0 social" in w for w in s.warnings)
    assert all(a["coverage"]["social_posts"] == 0 for a in s.report["assets"])
    assert s.report["assets"][0]["prediction"] is not None               # market-only prediction still works


def test_missing_model_yields_no_prediction_and_says_why():
    async def go(tmp):
        svc = ModelService.load(tmp / "none.joblib", tmp / "none.json")
        return await orchestrator(make_deps(svc)).run(HKEX)
    import pathlib
    import tempfile
    s = run(go(pathlib.Path(tempfile.mkdtemp())))
    assert by_name(s)["prediction"].status == "skipped"
    a = s.report["assets"][0]
    assert a["prediction"] is None and "artifact not found" in a["prediction_note"]


def test_market_data_failure_degrades_without_crashing():
    class NoData:
        name = "none"

        async def get_daily_bars(self, *a, **k):
            raise IntegrationError("provider down")
    s = run(orchestrator(make_deps(run_sync_model(), market_provider=NoData())).run(HKEX))
    assert by_name(s)["market_data"].status == "failed" and by_name(s)["feature_engineering"].status == "skipped"
    assert s.report is not None and s.status == "partial"


def run_sync_model():
    return asyncio.run(demo_model())


def test_unknown_exchange_rejected():
    try:
        run(orchestrator(make_deps(run_sync_model())).start_run("NYSE"))
    except Exception as e:
        assert "unknown exchange" in str(e)
    else:
        raise AssertionError


def test_predictions_are_evaluated_against_next_actual_session_and_baseline():
    async def go():
        deps = make_deps(await demo_model())
        await orchestrator(deps).run(HKEX)
        later = NOW + timedelta(days=2)                                  # new sessions have now closed
        deps.clock = lambda: later
        s2 = await orchestrator(deps).run(HKEX)
        return deps, s2
    deps, s2 = run(go())
    ev = run_sync(deps.repo.select("prediction_evaluations"))
    assert len(ev) == 5
    e = ev[0]
    assert e["actual_session_date"].isoformat() > "2026-11-10" and e["baseline_abs_error"] == abs(e["actual_return"])
    summ = s2.report["evaluation"]
    assert summ["n"] == 5 and summ["model"]["mae"] is not None and "baseline_zero_return" in summ
    assert any(h["actual_price"] for h in s2.report["assets"][0]["history"])


def test_features_exclude_posts_published_after_the_session_close():
    """A post at 17:00 HKT on the as-of day belongs to the NEXT session and must not enter today's features."""
    import pandas as pd

    from app.repositories.memory import MemoryRepository
    from app.workers.prediction import FeatureEngineeringWorker, FeaturePayload
    from tests.helpers import exchanges
    ex = exchanges().get(HKEX)
    cal = ex.calendar()
    bars = run(DemoMarketProvider().get_daily_bars("0700.HK", NOW.date() - timedelta(days=200), NOW.date(), cal))
    as_of = pd.Timestamp(cal.last_complete_session(NOW))
    late = pd.Timestamp(cal.bucket_date(NOW.replace(hour=9)))            # 17:00 HKT, after the close
    assert late > as_of
    rows = pd.DataFrame({"symbol": ["0700.HK"], "source_type": ["social"], "bucket_date": [late],
                         "sentiment_score": [0.9], "weight": [1.0], "author_id": ["a"], "engagement": [1.0]})
    res = run(FeatureEngineeringWorker(MemoryRepository()).execute(FeaturePayload({"0700.HK": bars}, rows, cal, NOW), {"run_id": "r"})).output
    assert res.rows[0].coverage["social_posts"] == 0 and res.rows[0].features["social_sentiment_mean"] != res.rows[0].features["social_sentiment_mean"]
