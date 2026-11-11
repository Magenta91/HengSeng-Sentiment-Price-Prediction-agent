import asyncio
import json

import pytest

pytest.importorskip("pydantic")

from app.core.exceptions import ExtractionError
from app.schemas.records import SocialPost
from app.services.extraction_service import ExtractionService
from tests.helpers import NOW

TEXT = "Long from 480 on Tencent, resistance 520. Bullish breakout."


class ScriptedLLM:
    model = "scripted"

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    async def complete(self, system, user, *, max_tokens=1000):
        self.prompts.append(user)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def good(**kw):
    d = {"asset_symbol": "0700.HK", "asset_name": "Tencent", "mentioned": True, "sentiment": "bullish", "sentiment_score": 0.7,
         "direction": "up", "position": None, "support_levels": [], "resistance_levels": [520], "target_price": None,
         "confidence": 0.8, "evidence_text": "Bullish breakout"}
    d.update(kw)
    return json.dumps(d)


def rec():
    return SocialPost("lihkg", "1", TEXT, NOW, author_id="u")


def extract(llm, **kw):
    return asyncio.run(ExtractionService(llm, **kw).extract(rec(), "0700.HK", "Tencent"))


def test_valid_output_with_fences_is_accepted():
    e = extract(ScriptedLLM(["```json\n" + good() + "\n```"]))
    assert e.sentiment == "bullish" and e.resistance_levels == [520] and e.model_name == "scripted"


def test_malformed_json_is_retried_with_error_feedback():
    llm = ScriptedLLM(["not json at all", '{"sentiment": "moonshot"}', good()])
    e = extract(llm)
    assert e is not None and len(llm.prompts) == 3 and "previous reply was invalid" in llm.prompts[1]


def test_schema_violations_exhaust_retry_budget():
    llm = ScriptedLLM([good(sentiment_score=5)])
    with pytest.raises(ExtractionError):
        extract(llm, max_attempts=2)
    assert len(llm.prompts) == 2


def test_missing_required_fields_rejected():
    with pytest.raises(ExtractionError):
        extract(ScriptedLLM([json.dumps({"asset_symbol": "0700.HK"})]), max_attempts=1)


def test_hallucinated_levels_position_and_target_are_removed():
    reply = good(support_levels=[455], resistance_levels=[520, 600], target_price=650,
                 position={"type": "long", "entry_price": 480, "evidence_text": "Long from 480"})
    e = extract(ScriptedLLM([reply]))
    assert e.support_levels == [] and e.resistance_levels == [520] and e.target_price is None
    assert e.position["entry_price"] == 480 and any("dropped" in n for n in e.guard_notes)
    e2 = extract(ScriptedLLM([good(position={"type": "short", "evidence_text": "I am short"})]))
    assert e2.position is None


def test_not_mentioned_returns_none():
    assert extract(ScriptedLLM([good(mentioned=False)])) is None


def test_full_pipeline_with_demo_llm_extracts_positions_and_levels():
    from app.demo.fixtures import DemoLLMClient
    from tests.helpers import demo_model, make_deps, orchestrator
    async def go():
        deps = make_deps(await demo_model(), extraction_service=ExtractionService(DemoLLMClient()))
        return await orchestrator(deps, extraction_max_items=60).run("HKEX")
    s = asyncio.run(go())
    assert s.status == "succeeded"
    a = s.report["assets"][0]
    assert a["intelligence"] and a["coverage"]["social_scored"] > 0
    assert any(i["position"] or i["resistance_levels"] for i in a["intelligence"])
    assert s.report["rankings"][0]["direction"] in {"up", "down", "mixed", "sideways", "unclear"}
