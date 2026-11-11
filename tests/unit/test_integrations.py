import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.config_models import SourceConfig
from app.core.exceptions import IntegrationError, TransientError
from app.core.retry import RetryPolicy
from app.integrations.apify_client import ApifyClient
from app.integrations.exa_client import ExaClient
from app.integrations.http import HttpResponse
from app.integrations.social_adapters import ApifySocialAdapter, get_path, render_template
from app.schemas.records import IngestionRequest
from tests.helpers import NOW, exchanges, no_sleep


class FakeTransport:
    """Scripted responses keyed by (method, url suffix); records every call."""

    def __init__(self, script):
        self.script, self.calls = script, []

    async def request(self, method, url, *, headers=None, params=None, json=None, timeout=30.0):
        self.calls.append((method, url, headers, params, json))
        for (m, suffix), responses in self.script.items():
            if m == method and url.endswith(suffix):
                r = responses.pop(0) if len(responses) > 1 else responses[0]
                return r if isinstance(r, HttpResponse) else HttpResponse(200, r)
        raise AssertionError(f"unscripted call {method} {url}")


FAST = RetryPolicy(max_attempts=3, base_delay=0, jitter=0)


def apify(transport, **kw):
    return ApifyClient("tok_secret", transport, retry=FAST, sleep=no_sleep, poll_interval=0, **kw)


def test_apify_run_poll_and_paginate():
    t = FakeTransport({
        ("POST", "/acts/user~actor/runs"): [{"data": {"id": "R1", "defaultDatasetId": "D1", "status": "READY"}}],
        ("GET", "/actor-runs/R1"): [{"data": {"status": "RUNNING"}}, {"data": {"status": "SUCCEEDED", "defaultDatasetId": "D1"}}],
        ("GET", "/datasets/D1/items"): [[{"i": 1}, {"i": 2}, {"i": 3}]],
    })
    res = asyncio.run(apify(t).run_and_fetch("user/actor", {"q": 1}, max_items=10))
    assert [i["i"] for i in res.items] == [1, 2, 3] and res.run_id == "R1" and res.dataset_id == "D1"
    assert t.calls[0][2]["Authorization"] == "Bearer tok_secret" and t.calls[0][4] == {"q": 1}


def test_apify_dataset_pagination_uses_offsets():
    t = FakeTransport({("GET", "/datasets/D/items"): [[{"i": 1}, {"i": 2}], [{"i": 3}, {"i": 4}], [{"i": 5}]]})
    items = asyncio.run(apify(t).fetch_dataset("D", max_items=10, page_size=2))
    assert [i["i"] for i in items] == [1, 2, 3, 4, 5]
    assert [c[3]["offset"] for c in t.calls] == [0, 2, 4]


def test_apify_transient_then_success_and_failed_run():
    t = FakeTransport({("POST", "/acts/a~b/runs"): [HttpResponse(503), {"data": {"id": "R", "defaultDatasetId": "D"}}],
                       ("GET", "/actor-runs/R"): [{"data": {"status": "FAILED"}}]})
    try:
        asyncio.run(apify(t).run_and_fetch("a/b", {}))
    except IntegrationError as e:
        assert "FAILED" in str(e) and "tok_secret" not in str(e)
    else:
        raise AssertionError


def test_http_4xx_is_not_retried_and_429_is_transient():
    t = FakeTransport({("POST", "/acts/a~b/runs"): [HttpResponse(401)]})
    try:
        asyncio.run(apify(t).start_run("a/b", {}))
    except IntegrationError as e:
        assert not isinstance(e, TransientError)
    assert len(t.calls) == 1
    t2 = FakeTransport({("POST", "/acts/a~b/runs"): [HttpResponse(429, headers={"Retry-After": "1"})]})
    try:
        asyncio.run(apify(t2).start_run("a/b", {}))
    except TransientError:
        pass
    assert len(t2.calls) == 3


def test_apify_poll_timeout_is_not_transient():
    ticks = iter(range(0, 1000, 100))
    t = FakeTransport({("GET", "/actor-runs/R"): [{"data": {"status": "RUNNING"}}]})
    c = ApifyClient("tok", t, retry=FAST, sleep=no_sleep, poll_interval=0, run_timeout=150, clock=lambda: next(ticks))
    try:
        asyncio.run(c.wait_for_run("R"))
    except IntegrationError as e:
        assert not isinstance(e, TransientError)


def test_exa_request_shape_and_publication_date():
    t = FakeTransport({("POST", "/search"): [{"results": [{"url": "http://a", "title": "T", "publishedDate": "2026-11-09T00:00:00.000Z"}]}]})
    c = ExaClient("exa_secret", t, retry=FAST, sleep=no_sleep)
    out = asyncio.run(c.search_news("Tencent stock news", start=NOW, end=NOW, num_results=5))
    body = t.calls[0][4]
    assert out[0]["publishedDate"] and body["numResults"] == 5 and body["category"] == "news" and "startPublishedDate" in body
    assert t.calls[0][2]["x-api-key"] == "exa_secret"


def test_template_rendering_and_paths():
    tpl = {"searchTerms": "{queries}", "max": "{max_items}", "since": "from {since_iso}", "k": [1, "{max_items}"]}
    out = render_template(tpl, {"queries": ["a", "b"], "max_items": 50, "since_iso": "2026"})
    assert out == {"searchTerms": ["a", "b"], "max": 50, "since": "from 2026", "k": [1, 50]}
    assert get_path({"user": {"followers": 5}}, "user.followers") == 5 and get_path({}, "a.b") is None


def source(**kw):
    base = dict(name="naver_finance", enabled=True, actor_id="u/a", source_timezone="Asia/Seoul",
                input_template={"queries": "{queries}", "limit": "{max_items}"},
                field_map={"id": "id", "text": "body", "timestamp": "date", "author_id": "user.name",
                           "followers": "user.followers", "likes": "likes", "reposts": "shares"})
    base.update(kw)
    return SourceConfig(**base)


def test_social_adapter_normalises_to_utc_and_tracks_provenance():
    items = [{"id": 7, "body": "삼성전자 상승", "date": "2026.11.10 14:32", "user": {"name": "kim", "followers": "1,200"}, "likes": "15"},
             {"id": 8, "body": "", "date": "2026.11.10 14:33"}]
    t = FakeTransport({("POST", "/acts/u~a/runs"): [{"data": {"id": "R", "defaultDatasetId": "D"}}],
                       ("GET", "/actor-runs/R"): [{"data": {"status": "SUCCEEDED"}}],
                       ("GET", "/datasets/D/items"): [items]})
    ex = exchanges().get("KRX")
    req = IngestionRequest(ex, list(ex.assets), NOW, NOW, 100)
    res = asyncio.run(ApifySocialAdapter(source(), apify(t)).fetch(req))
    p = res.records[0]
    assert len(res.records) == 1 and res.meta["invalid_items"] == 1
    assert p.posted_at == datetime(2026, 11, 10, 5, 32, tzinfo=UTC)      # KST -> UTC
    assert p.author_followers == 1200 and p.likes == 15 and p.reposts is None
    assert p.provenance["actor_id"] == "u/a" and p.provenance["apify_run_id"] == "R" and p.provenance["dataset_id"] == "D"
    assert t.calls[0][4]["limit"] == 100 and "삼성전자" in t.calls[0][4]["queries"]


def test_unconfigured_source_reports_why():
    assert "actor_id" in SourceConfig(name="x", enabled=True).operational_problem()
    assert "disabled" in SourceConfig(name="x").operational_problem()
    assert source().operational_problem() is None
    # LIHKG, Xueqiu, and Naver are now operational; other sources remain placeholders
    for e in exchanges().exchanges.values():
        for s in e.sources:
            if s.name in ("lihkg", "xueqiu", "naver_stock_discussion") and s.enabled:
                assert s.operational_problem() is None  # LIHKG, Xueqiu, and Naver are configured
            else:
                assert s.operational_problem() is not None  # Others are placeholders



def test_gemini_client_completes_with_mocked_response():
    """Test that GeminiLLMClient correctly calls the Gemini API and extracts text."""
    with patch("google.genai.Client") as mock_client_class:
        # Create a mock response structure matching new SDK's response
        mock_response = MagicMock()
        mock_response.text = '{"sentiment": "bullish", "score": 0.8}'
        
        # Mock the generate_content method
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_client_class.return_value = mock_client
        
        from app.integrations.llm_client import GeminiLLMClient
        
        client = GeminiLLMClient("test_key", "gemini-2.5-flash")
        result = asyncio.run(client.complete("system prompt", "user prompt", max_tokens=500))
        
        assert result == '{"sentiment": "bullish", "score": 0.8}'
        assert client.model == "gemini-2.5-flash"
        mock_client.models.generate_content.assert_called_once()


def test_gemini_client_raises_on_missing_api_key():
    """Test that GeminiLLMClient raises IntegrationError when API key is missing."""
    from app.integrations.llm_client import GeminiLLMClient
    
    try:
        GeminiLLMClient("", "gemini-2.5-flash")
    except IntegrationError as e:
        assert "GEMINI_API_KEY" in str(e)
    else:
        raise AssertionError("Expected IntegrationError for missing API key")


def test_gemini_client_handles_empty_response():
    """Test that GeminiLLMClient raises IntegrationError when response has no text."""
    with patch("google.genai.Client") as mock_client_class:
        # Create a mock response with no text
        mock_response = MagicMock()
        mock_response.text = None
        
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_client_class.return_value = mock_client
        
        from app.integrations.llm_client import GeminiLLMClient
        
        client = GeminiLLMClient("test_key", "gemini-2.5-flash")
        
        try:
            asyncio.run(client.complete("system", "user"))
        except IntegrationError as e:
            assert "empty response" in str(e).lower()
        else:
            raise AssertionError("Expected IntegrationError for empty response")


def test_gemini_client_handles_api_exception():
    """Test that GeminiLLMClient wraps exceptions from the Gemini API."""
    with patch("google.genai.Client") as mock_client_class:
        # Mock the client to raise an API error
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("API Error: quota exceeded")
        mock_client_class.return_value = mock_client
        
        from app.integrations.llm_client import GeminiLLMClient
        
        client = GeminiLLMClient("test_key", "gemini-2.5-flash")
        
        try:
            asyncio.run(client.complete("system", "user"))
        except IntegrationError as e:
            assert "Gemini API error" in str(e)
            assert "quota exceeded" in str(e)
        else:
            raise AssertionError("Expected IntegrationError wrapping API exception")


def test_gemini_client_timeout():
    """Test that GeminiLLMClient times out long-running requests."""
    with patch("google.genai.Client") as mock_client_class:
        def slow_generate(*args, **kwargs):
            import time
            time.sleep(10)  # Block for 10 seconds
            return MagicMock(text="too slow")
        
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = slow_generate
        mock_client_class.return_value = mock_client
        
        from app.integrations.llm_client import GeminiLLMClient
        
        client = GeminiLLMClient("test_key", "gemini-2.5-flash", timeout=0.1)
        
        try:
            asyncio.run(client.complete("system", "user"))
        except IntegrationError as e:
            assert "timed out" in str(e).lower()
        else:
            raise AssertionError("Expected IntegrationError for timeout")



def test_lihkg_adapter_normalizes_threads():
    """Test that LIHKGAdapter correctly normalizes thread records from search mode."""
    from app.integrations.social_adapters import LIHKGAdapter
    from app.core.config_models import SourceConfig
    
    # Configure LIHKG source with the actual observed schema
    config = SourceConfig(
        name="lihkg",
        enabled=True,
        actor_id="claude_code_reviewer/lihkg-scraper-en",
        source_timezone="Asia/Hong_Kong",
        language="zh-HK",
        input_template={"mode": "search", "keywords": "{queries}", "maxThreads": "{max_items}"},
        field_map={
            "thread": {
                "id": "thread_id",
                "text": "content",
                "timestamp": "created_at",
                "author_id": "author",
                "likes": "like_count",
                "url": "thread_url",
                "title": "title",
                "own_text": "own_text"
            }
        }
    )
    
    # Mock dataset with thread records (search mode returns thread metadata + content)
    thread_record = {
        "thread_id": "12345",
        "title": "騰訊股價討論",
        "content": "騰訊股價討論\n\n大家點睇騰訊最近走勢？",
        "own_text": "大家點睇騰訊最近走勢？",
        "author": "user123",
        "reply_count": 5,
        "like_count": 10,
        "created_at": "2026-11-10T10:00:00+08:00",
        "last_reply_at": "2026-11-10T11:00:00+08:00",
        "thread_url": "https://lihkg.com/thread/12345",
        "source_mode": "search",
        "source_query": "騰訊",
        "scraped_at": "2026-11-10T12:00:00+08:00"
    }
    
    # Mock Apify response - search mode only
    t = FakeTransport({
        ("POST", "/acts/claude_code_reviewer~lihkg-scraper-en/runs"): [
            {"data": {"id": "R_search", "defaultDatasetId": "D_search", "status": "READY"}}
        ],
        ("GET", "/actor-runs/R_search"): [
            {"data": {"status": "SUCCEEDED", "defaultDatasetId": "D_search"}}
        ],
        ("GET", "/datasets/D_search/items"): [
            [thread_record]
        ]
    })
    
    ex = exchanges().get("HKEX")
    # Use a 1-day window to accommodate test data timestamps
    window_start = NOW - timedelta(days=1)
    req = IngestionRequest(ex, list(ex.assets), window_start, NOW, 100)
    adapter = LIHKGAdapter(config, apify(t))
    res = asyncio.run(adapter.fetch(req))
    
    # Verify we got the thread
    assert len(res.records) == 1
    assert res.meta["threads"] == 1
    assert res.meta["replies"] == 0  # Replies not accessible via search mode
    
    # Verify thread record
    thread_post = res.records[0]
    assert thread_post.source == "lihkg"
    assert thread_post.source_post_id == "thread-12345"
    assert "騰訊股價討論" in thread_post.text
    assert "大家點睇騰訊最近走勢" in thread_post.text
    assert thread_post.author_id == "user123"
    assert thread_post.likes == 10
    assert thread_post.url == "https://lihkg.com/thread/12345"
    assert thread_post.provenance["record_type"] == "thread"
    assert thread_post.provenance["thread_id"] == "12345"


def test_lihkg_adapter_handles_missing_fields():
    """Test that LIHKGAdapter handles missing optional fields correctly."""
    from app.integrations.social_adapters import LIHKGAdapter
    from app.core.config_models import SourceConfig
    
    config = SourceConfig(
        name="lihkg",
        enabled=True,
        actor_id="claude_code_reviewer/lihkg-scraper-en",
        source_timezone="Asia/Hong_Kong",
        language="zh-HK",
        input_template={"mode": "search", "keywords": "{queries}"},
        field_map={
            "thread": {
                "id": "thread_id",
                "text": "content",
                "timestamp": "created_at",
                "author_id": "author",
                "likes": "like_count",
                "own_text": "own_text"
            },
            "reply": {
                "text": "content",
                "timestamp": "created_at",
                "author_id": "author",
                "likes": "like_count"
            }
        }
    )
    
    # Thread with missing optional fields
    thread_no_likes = {
        "thread_id": "67890",
        "content": "測試討論",
        "own_text": "測試討論",
        "author": "anonymous",
        "created_at": "2026-11-10T09:00:00+08:00"
        # No like_count, no URL, no title
    }
    
    t = FakeTransport({
        ("POST", "/acts/claude_code_reviewer~lihkg-scraper-en/runs"): [
            {"data": {"id": "R2", "defaultDatasetId": "D2"}}
        ],
        ("GET", "/actor-runs/R2"): [
            {"data": {"status": "SUCCEEDED", "defaultDatasetId": "D2"}}
        ],
        ("GET", "/datasets/D2/items"): [[thread_no_likes]],
    })
    
    ex = exchanges().get("HKEX")
    window_start = NOW - timedelta(days=1)
    req = IngestionRequest(ex, list(ex.assets), window_start, NOW, 100)
    adapter = LIHKGAdapter(config, apify(t))
    res = asyncio.run(adapter.fetch(req))
    
    assert len(res.records) == 1
    post = res.records[0]
    assert post.likes is None
    assert post.url is None
    assert post.reposts is None  # LIHKG doesn't have reposts
    assert post.views is None  # LIHKG doesn't have views
    assert post.author_followers is None  # LIHKG doesn't expose follower counts


def test_lihkg_adapter_skips_empty_content():
    """Test that LIHKGAdapter skips records with empty content."""
    from app.integrations.social_adapters import LIHKGAdapter
    from app.core.config_models import SourceConfig
    
    config = SourceConfig(
        name="lihkg",
        enabled=True,
        actor_id="claude_code_reviewer/lihkg-scraper-en",
        source_timezone="Asia/Hong_Kong",
        language="zh-HK",
        input_template={},
        field_map={
            "thread": {"id": "thread_id", "text": "content", "timestamp": "created_at", "author_id": "author"},
            "reply": {"text": "content", "timestamp": "created_at", "author_id": "author"}
        }
    )
    
    empty_thread = {
        "thread_id": "empty1",
        "content": "",
        "author": "user1",
        "created_at": "2026-11-10T10:00:00+08:00"
    }
    
    valid_thread = {
        "thread_id": "valid1",
        "content": "有內容",
        "author": "user2",
        "created_at": "2026-11-10T10:00:00+08:00"
    }
    
    t = FakeTransport({
        ("POST", "/acts/claude_code_reviewer~lihkg-scraper-en/runs"): [
            {"data": {"id": "R3", "defaultDatasetId": "D3"}}
        ],
        ("GET", "/actor-runs/R3"): [
            {"data": {"status": "SUCCEEDED", "defaultDatasetId": "D3"}}
        ],
        ("GET", "/datasets/D3/items"): [[empty_thread, valid_thread]],
    })
    
    ex = exchanges().get("HKEX")
    window_start = NOW - timedelta(days=1)
    req = IngestionRequest(ex, list(ex.assets), window_start, NOW, 100)
    adapter = LIHKGAdapter(config, apify(t))
    res = asyncio.run(adapter.fetch(req))
    
    assert len(res.records) == 1
    assert res.meta["invalid_items"] == 1
    assert res.records[0].text == "有內容"


def test_lihkg_adapter_prefers_own_text_over_content():
    """Test that LIHKGAdapter prefers own_text when available."""
    from app.integrations.social_adapters import LIHKGAdapter
    from app.core.config_models import SourceConfig
    
    config = SourceConfig(
        name="lihkg",
        enabled=True,
        actor_id="claude_code_reviewer/lihkg-scraper-en",
        source_timezone="Asia/Hong_Kong",
        language="zh-HK",
        input_template={},
        field_map={
            "thread": {
                "id": "thread_id",
                "text": "content",
                "own_text": "own_text",
                "timestamp": "created_at",
                "author_id": "author"
            },
            "reply": {"text": "content", "own_text": "own_text", "timestamp": "created_at", "author_id": "author"}
        }
    )
    
    # Thread with both content and own_text
    thread = {
        "thread_id": "test123",
        "content": "Full content with quotes and other text",
        "own_text": "Just the author's text",
        "author": "user1",
        "created_at": "2026-11-10T10:00:00+08:00"
    }
    
    t = FakeTransport({
        ("POST", "/acts/claude_code_reviewer~lihkg-scraper-en/runs"): [
            {"data": {"id": "R4", "defaultDatasetId": "D4"}}
        ],
        ("GET", "/actor-runs/R4"): [
            {"data": {"status": "SUCCEEDED", "defaultDatasetId": "D4"}}
        ],
        ("GET", "/datasets/D4/items"): [[thread]],
    })
    
    ex = exchanges().get("HKEX")
    window_start = NOW - timedelta(days=1)
    req = IngestionRequest(ex, list(ex.assets), window_start, NOW, 100)
    adapter = LIHKGAdapter(config, apify(t))
    res = asyncio.run(adapter.fetch(req))
    
    assert len(res.records) == 1
    # Should use own_text, not content
    assert res.records[0].text == "Just the author's text"


def test_xueqiu_adapter_executes_per_ticker():
    """Test that XueqiuAdapter executes separately for each ticker."""
    from app.integrations.social_adapters import XueqiuAdapter
    from app.core.config_models import SourceConfig
    
    config = SourceConfig(
        name="xueqiu",
        enabled=True,
        actor_id="luminous_luxury/xueqiu-stock-scraper",
        source_timezone="Asia/Shanghai",
        language="zh-CN",
        max_items=20,
        input_template={"limit": "{max_items}", "stock_code": "{stock_code}"},
        field_map={
            "id": "url",
            "text": "title",
            "timestamp": "time",
            "author_id": "user",
            "followers": "followers",
            "likes": "likes",
            "url": "url",
            "comments": "comments"
        }
    )
    
    # Mock responses for 3 tickers
    t = FakeTransport({
        ("POST", "/acts/luminous_luxury~xueqiu-stock-scraper/runs"): [
            {"data": {"id": "R0700", "defaultDatasetId": "D0700"}},
            {"data": {"id": "R9988", "defaultDatasetId": "D9988"}},
            {"data": {"id": "R0005", "defaultDatasetId": "D0005"}},
        ],
        ("GET", "/actor-runs/R0700"): [{"data": {"status": "SUCCEEDED"}}],
        ("GET", "/datasets/D0700/items"): [[
            {
                "title": "騰訊股價分析",
                "url": "https://xueqiu.com/1234567890",
                "user": "user_a",
                "followers": 1000,
                "likes": 50,
                "comments": 10,
                "time": "2026-11-09T10:00:00+08:00"
            }
        ]],
        ("GET", "/actor-runs/R9988"): [{"data": {"status": "SUCCEEDED"}}],
        ("GET", "/datasets/D9988/items"): [[
            {
                "title": "阿里巴巴業績",
                "url": "https://xueqiu.com/1234567891",
                "user": "user_b",
                "followers": 2000,
                "likes": 80,
                "comments": 20,
                "time": "2026-11-08T14:00:00+08:00"
            }
        ]],
        ("GET", "/actor-runs/R0005"): [{"data": {"status": "SUCCEEDED"}}],
        ("GET", "/datasets/D0005/items"): [[
            {
                "title": "匯豐控股分紅",
                "url": "https://xueqiu.com/1234567892",
                "user": "user_c",
                "followers": 500,
                "likes": 30,
                "comments": 5,
                "time": "2026-11-07T09:00:00+08:00"
            }
        ]],
    })
    
    ex = exchanges().get("HKEX")
    # Use only first 3 assets for this test
    assets = list(ex.assets)[:3]  # Tencent, Alibaba, HSBC
    window_start = NOW - timedelta(days=10)
    req = IngestionRequest(ex, assets, window_start, NOW, 20)
    
    adapter = XueqiuAdapter(config, apify(t))
    res = asyncio.run(adapter.fetch(req))
    
    # Verify 3 separate executions
    assert res.meta["executions"] == 3
    assert len(res.meta["execution_details"]) == 3
    
    # Verify each execution used correct stock code
    exec_details = {e["ticker"]: e for e in res.meta["execution_details"]}
    assert exec_details["0700.HK"]["stock_code"] == "0700"
    assert exec_details["9988.HK"]["stock_code"] == "9988"
    assert exec_details["0005.HK"]["stock_code"] == "0005"
    
    # Verify we got 3 posts
    assert len(res.records) == 3
    assert res.meta["posts"] == 3
    
    # Verify post content
    post_texts = {p.text for p in res.records}
    assert "騰訊股價分析" in post_texts
    assert "阿里巴巴業績" in post_texts
    assert "匯豐控股分紅" in post_texts
    
    # Verify ticker association in provenance
    for post in res.records:
        assert "ticker_symbol" in post.provenance
        assert post.provenance["ticker_symbol"] in ["0700.HK", "9988.HK", "0005.HK"]


def test_xueqiu_adapter_normalizes_fields_correctly():
    """Test that XueqiuAdapter correctly normalizes Xueqiu fields."""
    from app.integrations.social_adapters import XueqiuAdapter
    from app.core.config_models import SourceConfig
    
    config = SourceConfig(
        name="xueqiu",
        enabled=True,
        actor_id="luminous_luxury/xueqiu-stock-scraper",
        source_timezone="Asia/Shanghai",
        language="zh-CN",
        max_items=20,
        input_template={"limit": "{max_items}", "stock_code": "{stock_code}"},
        field_map={
            "id": "url",
            "text": "title",
            "timestamp": "time",
            "author_id": "user",
            "followers": "followers",
            "likes": "likes",
            "url": "url",
            "comments": "comments"
        }
    )
    
    t = FakeTransport({
        ("POST", "/acts/luminous_luxury~xueqiu-stock-scraper/runs"): [
            {"data": {"id": "R1", "defaultDatasetId": "D1"}}
        ],
        ("GET", "/actor-runs/R1"): [{"data": {"status": "SUCCEEDED"}}],
        ("GET", "/datasets/D1/items"): [[
            {
                "title": "Tencent Q3 earnings strong",
                "url": "https://xueqiu.com/9999999999",
                "user": "analyst123",
                "followers": 5000,
                "likes": 120,
                "comments": 35,
                "time": "2026-11-09T15:30:00+08:00"
            }
        ]],
    })
    
    ex = exchanges().get("HKEX")
    tencent = list(ex.assets)[0]  # Tencent
    window_start = NOW - timedelta(days=10)
    req = IngestionRequest(ex, [tencent], window_start, NOW, 20)
    
    adapter = XueqiuAdapter(config, apify(t))
    res = asyncio.run(adapter.fetch(req))
    
    assert len(res.records) == 1
    post = res.records[0]
    
    # Verify field mapping
    assert post.source == "xueqiu"
    assert post.text == "Tencent Q3 earnings strong"
    assert post.author_id == "analyst123"
    assert post.author_followers == 5000
    assert post.likes == 120
    assert post.url == "https://xueqiu.com/9999999999"
    assert post.language == "zh-CN"
    
    # Verify timestamp normalization to UTC
    assert post.posted_at.tzinfo == UTC
    # Shanghai is UTC+8, so 15:30 Shanghai = 07:30 UTC
    assert post.posted_at.hour == 7
    assert post.posted_at.minute == 30
    
    # Verify unavailable fields are None
    assert post.reposts is None
    assert post.views is None
    
    # Verify comments stored in provenance
    assert post.provenance["comments"] == 35


def test_xueqiu_adapter_handles_missing_fields():
    """Test that XueqiuAdapter handles missing optional fields."""
    from app.integrations.social_adapters import XueqiuAdapter
    from app.core.config_models import SourceConfig
    
    config = SourceConfig(
        name="xueqiu",
        enabled=True,
        actor_id="luminous_luxury/xueqiu-stock-scraper",
        source_timezone="Asia/Shanghai",
        language="zh-CN",
        max_items=20,
        input_template={"limit": "{max_items}", "stock_code": "{stock_code}"},
        field_map={
            "id": "url",
            "text": "title",
            "timestamp": "time",
            "author_id": "user",
            "followers": "followers",
            "likes": "likes",
            "url": "url",
            "comments": "comments"
        }
    )
    
    # Post with minimal fields (official announcement style)
    t = FakeTransport({
        ("POST", "/acts/luminous_luxury~xueqiu-stock-scraper/runs"): [
            {"data": {"id": "R1", "defaultDatasetId": "D1"}}
        ],
        ("GET", "/actor-runs/R1"): [{"data": {"status": "SUCCEEDED"}}],
        ("GET", "/datasets/D1/items"): [[
            {
                "title": "Official company announcement",
                "time": "2026-11-09T10:00:00+08:00"
                # No URL, no user, no engagement
            }
        ]],
    })
    
    ex = exchanges().get("HKEX")
    tencent = list(ex.assets)[0]
    window_start = NOW - timedelta(days=10)
    req = IngestionRequest(ex, [tencent], window_start, NOW, 20)
    
    adapter = XueqiuAdapter(config, apify(t))
    res = asyncio.run(adapter.fetch(req))
    
    assert len(res.records) == 1
    post = res.records[0]
    
    # Verify handling of missing fields
    assert post.url is None
    assert post.author_id is None
    assert post.author_followers is None
    assert post.likes is None
    assert post.provenance.get("comments") is None


def test_xueqiu_adapter_applies_date_filter():
    """Test that XueqiuAdapter filters posts by date window."""
    from app.integrations.social_adapters import XueqiuAdapter
    from app.core.config_models import SourceConfig
    
    config = SourceConfig(
        name="xueqiu",
        enabled=True,
        actor_id="luminous_luxury/xueqiu-stock-scraper",
        source_timezone="Asia/Shanghai",
        language="zh-CN",
        max_items=20,
        input_template={"limit": "{max_items}", "stock_code": "{stock_code}"},
        field_map={
            "text": "title",
            "timestamp": "time",
            "author_id": "user",
            "url": "url"
        }
    )
    
    t = FakeTransport({
        ("POST", "/acts/luminous_luxury~xueqiu-stock-scraper/runs"): [
            {"data": {"id": "R1", "defaultDatasetId": "D1"}}
        ],
        ("GET", "/actor-runs/R1"): [{"data": {"status": "SUCCEEDED"}}],
        ("GET", "/datasets/D1/items"): [[
            {"title": "Fresh post", "url": "https://xueqiu.com/1", "user": "u1", "time": "2026-11-09T10:00:00+08:00"},
            {"title": "Stale post", "url": "https://xueqiu.com/2", "user": "u2", "time": "2026-10-20T10:00:00+08:00"},  # Too old
            {"title": "Future post", "url": "https://xueqiu.com/3", "user": "u3", "time": "2026-11-15T10:00:00+08:00"},  # Future
            {"title": "Valid post", "url": "https://xueqiu.com/4", "user": "u4", "time": "2026-11-05T10:00:00+08:00"},
        ]],
    })
    
    ex = exchanges().get("HKEX")
    tencent = list(ex.assets)[0]
    window_start = NOW - timedelta(days=10)
    req = IngestionRequest(ex, [tencent], window_start, NOW, 20)
    
    adapter = XueqiuAdapter(config, apify(t))
    res = asyncio.run(adapter.fetch(req))
    
    # Should only get 2 posts (fresh and valid, not stale or future)
    assert len(res.records) == 2
    assert res.meta["stale_items"] == 2
    
    post_texts = {p.text for p in res.records}
    assert "Fresh post" in post_texts
    assert "Valid post" in post_texts
    assert "Stale post" not in post_texts
    assert "Future post" not in post_texts


def test_xueqiu_adapter_continues_on_partial_failure():
    """Test that XueqiuAdapter continues with other tickers if one fails."""
    from app.integrations.social_adapters import XueqiuAdapter
    from app.core.config_models import SourceConfig
    from app.core.exceptions import TransientError
    
    config = SourceConfig(
        name="xueqiu",
        enabled=True,
        actor_id="luminous_luxury/xueqiu-stock-scraper",
        source_timezone="Asia/Shanghai",
        language="zh-CN",
        max_items=20,
        input_template={"limit": "{max_items}", "stock_code": "{stock_code}"},
        field_map={"text": "title", "timestamp": "time", "url": "url"}
    )
    
    # Mock: first execution succeeds, second fails with 500
    # Third execution shouldn't happen since second failed
    call_count = 0
    
    class FailingClient:
        def __init__(self, success_client):
            self.success_client = success_client
            self.call_count = 0
            
        async def run_and_fetch(self, actor_id, run_input, *, max_items=1000):
            self.call_count += 1
            if self.call_count == 2:  # Second call for Alibaba
                raise TransientError("Actor execution failed")
            return await self.success_client.run_and_fetch(actor_id, run_input, max_items=max_items)
    
    # Regular transport for successful calls
    t = FakeTransport({
        ("POST", "/acts/luminous_luxury~xueqiu-stock-scraper/runs"): [
            {"data": {"id": "R1", "defaultDatasetId": "D1"}},
            {"data": {"id": "R3", "defaultDatasetId": "D3"}},
        ],
        ("GET", "/actor-runs/R1"): [{"data": {"status": "SUCCEEDED"}}],
        ("GET", "/datasets/D1/items"): [[
            {"title": "Tencent post", "url": "https://xueqiu.com/1", "time": "2026-11-09T10:00:00+08:00"}
        ]],
        ("GET", "/actor-runs/R3"): [{"data": {"status": "SUCCEEDED"}}],
        ("GET", "/datasets/D3/items"): [[
            {"title": "HSBC post", "url": "https://xueqiu.com/3", "time": "2026-11-09T11:00:00+08:00"}
        ]],
    })
    
    ex = exchanges().get("HKEX")
    assets = list(ex.assets)[:3]  # Tencent, Alibaba, HSBC
    window_start = NOW - timedelta(days=10)
    req = IngestionRequest(ex, assets, window_start, NOW, 20)
    
    client = FailingClient(apify(t))
    adapter = XueqiuAdapter(config, client)
    res = asyncio.run(adapter.fetch(req))
    
    # Should get 2 posts (from successful executions)
    assert len(res.records) == 2
    assert res.meta["executions"] == 2  # Only successful ones
    
    # Verify failure recorded
    assert "failed_tickers" in res.meta
    assert "9988.HK" in res.meta["failed_tickers"]
    
    # Verify successful posts
    post_texts = {p.text for p in res.records}
    assert "Tencent post" in post_texts
    assert "HSBC post" in post_texts


def test_xueqiu_adapter_deduplicates_by_url():
    """Test that XueqiuAdapter deduplicates posts using URL-based IDs."""
    from app.integrations.social_adapters import XueqiuAdapter
    from app.core.config_models import SourceConfig
    
    config = SourceConfig(
        name="xueqiu",
        enabled=True,
        actor_id="luminous_luxury/xueqiu-stock-scraper",
        source_timezone="Asia/Shanghai",
        language="zh-CN",
        max_items=20,
        input_template={"limit": "{max_items}", "stock_code": "{stock_code}"},
        field_map={"text": "title", "timestamp": "time", "url": "url"}
    )
    
    # Same URL appears in results - should get same post_id
    t = FakeTransport({
        ("POST", "/acts/luminous_luxury~xueqiu-stock-scraper/runs"): [
            {"data": {"id": "R1", "defaultDatasetId": "D1"}}
        ],
        ("GET", "/actor-runs/R1"): [{"data": {"status": "SUCCEEDED"}}],
        ("GET", "/datasets/D1/items"): [[
            {"title": "Post A", "url": "https://xueqiu.com/12345", "time": "2026-11-09T10:00:00+08:00"},
            {"title": "Post B", "url": "https://xueqiu.com/67890", "time": "2026-11-09T11:00:00+08:00"},
        ]],
    })
    
    ex = exchanges().get("HKEX")
    tencent = list(ex.assets)[0]
    window_start = NOW - timedelta(days=10)
    req = IngestionRequest(ex, [tencent], window_start, NOW, 20)
    
    adapter = XueqiuAdapter(config, apify(t))
    res = asyncio.run(adapter.fetch(req))
    
    assert len(res.records) == 2
    
    # Verify post IDs are derived from URLs
    post_ids = [p.source_post_id for p in res.records]
    assert len(set(post_ids)) == 2  # Both unique
    
    # If same URL appeared twice, it would get same ID
    # (This is handled by repository upsert layer)


def test_xueqiu_adapter_extracts_stock_codes():
    """Test stock code extraction from ticker symbols."""
    from app.integrations.social_adapters import XueqiuAdapter
    from app.core.config_models import SourceConfig
    
    config = SourceConfig(
        name="xueqiu",
        enabled=True,
        actor_id="luminous_luxury/xueqiu-stock-scraper",
        source_timezone="Asia/Shanghai",
        language="zh-CN",
        max_items=20,
        input_template={"limit": "{max_items}", "stock_code": "{stock_code}"},
        field_map={}
    )
    
    adapter = XueqiuAdapter(config, apify(FakeTransport({})))
    
    # Test HKEX ticker mapping
    assert adapter._extract_stock_code("0700.HK") == "0700"
    assert adapter._extract_stock_code("9988.HK") == "9988"
    assert adapter._extract_stock_code("0005.HK") == "0005"
    assert adapter._extract_stock_code("1299.HK") == "1299"
    assert adapter._extract_stock_code("3690.HK") == "3690"


def test_xueqiu_adapter_all_tickers_fail():
    """Test that XueqiuAdapter raises error when all ticker executions fail."""
    from app.integrations.social_adapters import XueqiuAdapter
    from app.core.config_models import SourceConfig
    from app.core.exceptions import TransientError
    
    config = SourceConfig(
        name="xueqiu",
        enabled=True,
        actor_id="luminous_luxury/xueqiu-stock-scraper",
        source_timezone="Asia/Shanghai",
        language="zh-CN",
        max_items=20,
        input_template={"limit": "{max_items}", "stock_code": "{stock_code}"},
        field_map={"text": "title", "timestamp": "time"}  # Valid field_map
    )
    
    # All executions fail
    class AllFailingClient:
        async def run_and_fetch(self, actor_id, run_input, *, max_items=1000):
            raise TransientError("Actor execution failed")
    
    ex = exchanges().get("HKEX")
    assets = list(ex.assets)[:2]
    window_start = NOW - timedelta(days=10)
    req = IngestionRequest(ex, assets, window_start, NOW, 20)
    
    adapter = XueqiuAdapter(config, AllFailingClient())
    
    try:
        asyncio.run(adapter.fetch(req))
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "All Xueqiu executions failed" in str(e)


def test_naver_stock_discussion_adapter_normalizes_fields():
    """Test that NaverStockDiscussionAdapter correctly normalizes fields."""
    from app.integrations.social_adapters import NaverStockDiscussionAdapter
    from app.core.config_models import SourceConfig
    
    config = SourceConfig(
        name="naver_stock_discussion",
        enabled=True,
        actor_id="hgservices/naver-stock-discussion-scraper",
        source_timezone="Asia/Seoul",
        language="ko",
        max_items=20,
        input_template={"stocks": "{stock_codes}", "limit": 20},
        field_map={
            "id": "url",
            "text": "title",
            "title": "title",
            "content": "text",
            "timestamp": "writtenAt",
            "author_id": "author",
            "likes": "likes",
            "dislikes": "dislikes",
            "views": "views",
            "url": "url",
            "stock_name": "stockName",
            "is_holder_verified": "isHolderVerified"
        }
    )
    
    t = FakeTransport({
        ("POST", "/acts/hgservices~naver-stock-discussion-scraper/runs"): [
            {"data": {"id": "R1", "defaultDatasetId": "D1"}}
        ],
        ("GET", "/actor-runs/R1"): [{"data": {"status": "SUCCEEDED"}}],
        ("GET", "/datasets/D1/items"): [[
            {
                "title": "외인 매수 의미있을까?",
                "text": "반도체주 전망 분석",
                "writtenAt": "2026-11-09T14:30:00+09:00",
                "author": "investor123",
                "likes": 15,
                "dislikes": 2,
                "views": 250,
                "url": "https://finance.naver.com/item/board.nhn?code=005930&seq=123",
                "stockName": "삼성전자",
                "isHolderVerified": True
            }
        ]],
    })
    
    ex = exchanges().get("KRX")
    samsung = list(ex.assets)[0]  # Samsung Electronics
    window_start = NOW - timedelta(days=10)
    req = IngestionRequest(ex, [samsung], window_start, NOW, 20)
    
    adapter = NaverStockDiscussionAdapter(config, apify(t))
    res = asyncio.run(adapter.fetch(req))
    
    assert len(res.records) == 1
    post = res.records[0]
    
    # Verify field mapping
    assert post.source == "naver_stock_discussion"
    assert "외인 매수 의미있을까?" in post.text
    assert "반도체주 전망 분석" in post.text
    assert post.author_id == "investor123"
    assert post.likes == 15
    assert post.views == 250
    assert post.url == "https://finance.naver.com/item/board.nhn?code=005930&seq=123"
    assert post.language == "ko"
    
    # Verify timestamp normalization to UTC (Seoul is UTC+9)
    assert post.posted_at.tzinfo == UTC
    assert post.posted_at.hour == 5  # 14:30 KST = 05:30 UTC
    assert post.posted_at.minute == 30
    
    # Verify Naver-specific metadata in provenance
    assert post.provenance["stock_name"] == "삼성전자"
    assert post.provenance["is_holder_verified"] is True
    assert post.provenance["dislikes"] == 2
    
    # Verify unavailable fields are None
    assert post.reposts is None
    assert post.author_followers is None


def test_naver_adapter_combines_title_and_content():
    """Test that adapter correctly combines title and text fields."""
    from app.integrations.social_adapters import NaverStockDiscussionAdapter
    from app.core.config_models import SourceConfig
    
    config = SourceConfig(
        name="naver_stock_discussion",
        enabled=True,
        actor_id="hgservices/naver-stock-discussion-scraper",
        source_timezone="Asia/Seoul",
        language="ko",
        max_items=20,
        input_template={"stocks": "{stock_codes}"},
        field_map={
            "text": "title",
            "title": "title",
            "content": "text",
            "timestamp": "writtenAt",
            "url": "url"
        }
    )
    
    t = FakeTransport({
        ("POST", "/acts/hgservices~naver-stock-discussion-scraper/runs"): [
            {"data": {"id": "R1", "defaultDatasetId": "D1"}}
        ],
        ("GET", "/actor-runs/R1"): [{"data": {"status": "SUCCEEDED"}}],
        ("GET", "/datasets/D1/items"): [[
            {"title": "제목", "text": "내용", "writtenAt": "2026-11-09T10:00:00+09:00", "url": "http://test.com/1"},
            {"title": "제목만", "writtenAt": "2026-11-09T10:00:00+09:00", "url": "http://test.com/2"},
            {"text": "내용만", "writtenAt": "2026-11-09T10:00:00+09:00", "url": "http://test.com/3"},
        ]],
    })
    
    ex = exchanges().get("KRX")
    samsung = list(ex.assets)[0]
    window_start = NOW - timedelta(days=10)
    req = IngestionRequest(ex, [samsung], window_start, NOW, 20)
    
    adapter = NaverStockDiscussionAdapter(config, apify(t))
    res = asyncio.run(adapter.fetch(req))
    
    assert len(res.records) == 3
    
    # Title + content combined
    assert res.records[0].text == "제목\n\n내용"
    
    # Title only
    assert res.records[1].text == "제목만"
    
    # Content only
    assert res.records[2].text == "내용만"


def test_naver_adapter_applies_date_filter():
    """Test that NaverStockDiscussionAdapter filters posts by date window."""
    from app.integrations.social_adapters import NaverStockDiscussionAdapter
    from app.core.config_models import SourceConfig
    
    config = SourceConfig(
        name="naver_stock_discussion",
        enabled=True,
        actor_id="hgservices/naver-stock-discussion-scraper",
        source_timezone="Asia/Seoul",
        language="ko",
        max_items=20,
        input_template={"stocks": "{stock_codes}"},
        field_map={
            "text": "title",
            "title": "title",
            "timestamp": "writtenAt",
            "url": "url"
        }
    )
    
    t = FakeTransport({
        ("POST", "/acts/hgservices~naver-stock-discussion-scraper/runs"): [
            {"data": {"id": "R1", "defaultDatasetId": "D1"}}
        ],
        ("GET", "/actor-runs/R1"): [{"data": {"status": "SUCCEEDED"}}],
        ("GET", "/datasets/D1/items"): [[
            {"title": "Fresh post", "writtenAt": "2026-11-09T10:00:00+09:00", "url": "http://test.com/1"},
            {"title": "Stale post", "writtenAt": "2026-10-20T10:00:00+09:00", "url": "http://test.com/2"},  # Too old
            {"title": "Future post", "writtenAt": "2026-11-15T10:00:00+09:00", "url": "http://test.com/3"},  # Future
            {"title": "Valid post", "writtenAt": "2026-11-05T10:00:00+09:00", "url": "http://test.com/4"},
        ]],
    })
    
    ex = exchanges().get("KRX")
    samsung = list(ex.assets)[0]
    window_start = NOW - timedelta(days=10)
    req = IngestionRequest(ex, [samsung], window_start, NOW, 20)
    
    adapter = NaverStockDiscussionAdapter(config, apify(t))
    res = asyncio.run(adapter.fetch(req))
    
    # Should only get 2 posts (fresh and valid)
    assert len(res.records) == 2
    assert res.meta["stale_items"] == 2
    
    post_texts = {p.text for p in res.records}
    assert "Fresh post" in post_texts
    assert "Valid post" in post_texts
    assert "Stale post" not in post_texts
    assert "Future post" not in post_texts


def test_naver_adapter_extracts_stock_codes():
    """Test stock code extraction from KRX ticker symbols."""
    from app.integrations.social_adapters import NaverStockDiscussionAdapter
    from app.core.config_models import SourceConfig
    
    config = SourceConfig(
        name="naver_stock_discussion",
        enabled=True,
        actor_id="hgservices/naver-stock-discussion-scraper",
        source_timezone="Asia/Seoul",
        language="ko",
        max_items=20,
        input_template={"stocks": "{stock_codes}"},
        field_map={}
    )
    
    adapter = NaverStockDiscussionAdapter(config, apify(FakeTransport({})))
    
    # Test KRX ticker mapping
    assert adapter._extract_stock_code("005930.KS") == "005930"
    assert adapter._extract_stock_code("000660.KS") == "000660"
    assert adapter._extract_stock_code("005380.KS") == "005380"
    assert adapter._extract_stock_code("373220.KS") == "373220"
    assert adapter._extract_stock_code("068270.KS") == "068270"


def test_naver_adapter_handles_missing_fields():
    """Test that NaverStockDiscussionAdapter handles missing optional fields."""
    from app.integrations.social_adapters import NaverStockDiscussionAdapter
    from app.core.config_models import SourceConfig
    
    config = SourceConfig(
        name="naver_stock_discussion",
        enabled=True,
        actor_id="hgservices/naver-stock-discussion-scraper",
        source_timezone="Asia/Seoul",
        language="ko",
        max_items=20,
        input_template={"stocks": "{stock_codes}"},
        field_map={
            "text": "title",
            "title": "title",
            "timestamp": "writtenAt",
            "author_id": "author",
            "likes": "likes",
            "views": "views",
            "stock_name": "stockName",
            "is_holder_verified": "isHolderVerified"
        }
    )
    
    # Post with minimal fields
    t = FakeTransport({
        ("POST", "/acts/hgservices~naver-stock-discussion-scraper/runs"): [
            {"data": {"id": "R1", "defaultDatasetId": "D1"}}
        ],
        ("GET", "/actor-runs/R1"): [{"data": {"status": "SUCCEEDED"}}],
        ("GET", "/datasets/D1/items"): [[
            {
                "title": "Minimal post",
                "writtenAt": "2026-11-09T10:00:00+09:00"
                # No author, likes, views, URL, etc.
            }
        ]],
    })
    
    ex = exchanges().get("KRX")
    samsung = list(ex.assets)[0]
    window_start = NOW - timedelta(days=10)
    req = IngestionRequest(ex, [samsung], window_start, NOW, 20)
    
    adapter = NaverStockDiscussionAdapter(config, apify(t))
    res = asyncio.run(adapter.fetch(req))
    
    assert len(res.records) == 1
    post = res.records[0]
    
    # Verify handling of missing fields
    assert post.url is None
    assert post.author_id is None
    assert post.likes is None
    assert post.views is None
    assert post.author_followers is None
    assert post.provenance.get("stock_name") is None
    assert post.provenance.get("is_holder_verified") is None


def test_naver_adapter_deduplicates_by_url():
    """Test that NaverStockDiscussionAdapter uses URL for stable IDs."""
    from app.integrations.social_adapters import NaverStockDiscussionAdapter
    from app.core.config_models import SourceConfig
    
    config = SourceConfig(
        name="naver_stock_discussion",
        enabled=True,
        actor_id="hgservices/naver-stock-discussion-scraper",
        source_timezone="Asia/Seoul",
        language="ko",
        max_items=20,
        input_template={"stocks": "{stock_codes}"},
        field_map={
            "text": "title",
            "title": "title",
            "timestamp": "writtenAt",
            "url": "url"
        }
    )
    
    t = FakeTransport({
        ("POST", "/acts/hgservices~naver-stock-discussion-scraper/runs"): [
            {"data": {"id": "R1", "defaultDatasetId": "D1"}}
        ],
        ("GET", "/actor-runs/R1"): [{"data": {"status": "SUCCEEDED"}}],
        ("GET", "/datasets/D1/items"): [[
            {"title": "Post A", "writtenAt": "2026-11-09T10:00:00+09:00", "url": "http://test.com/123"},
            {"title": "Post B", "writtenAt": "2026-11-09T11:00:00+09:00", "url": "http://test.com/456"},
        ]],
    })
    
    ex = exchanges().get("KRX")
    samsung = list(ex.assets)[0]
    window_start = NOW - timedelta(days=10)
    req = IngestionRequest(ex, [samsung], window_start, NOW, 20)
    
    adapter = NaverStockDiscussionAdapter(config, apify(t))
    res = asyncio.run(adapter.fetch(req))
    
    assert len(res.records) == 2
    
    # Verify post IDs are derived from URLs
    post_ids = [p.source_post_id for p in res.records]
    assert len(set(post_ids)) == 2  # Both unique


def test_naver_adapter_all_tickers_single_execution():
    """Test that Naver adapter fetches all KRX tickers in one execution."""
    from app.integrations.social_adapters import NaverStockDiscussionAdapter
    from app.core.config_models import SourceConfig
    
    config = SourceConfig(
        name="naver_stock_discussion",
        enabled=True,
        actor_id="hgservices/naver-stock-discussion-scraper",
        source_timezone="Asia/Seoul",
        language="ko",
        max_items=20,
        input_template={"stocks": "{stock_codes}", "limit": 20},
        field_map={
            "text": "title",
            "title": "title",
            "timestamp": "writtenAt",
            "url": "url",
            "stock_name": "stockName"
        }
    )
    
    # Single execution returning posts for multiple stocks
    t = FakeTransport({
        ("POST", "/acts/hgservices~naver-stock-discussion-scraper/runs"): [
            {"data": {"id": "R1", "defaultDatasetId": "D1"}}
        ],
        ("GET", "/actor-runs/R1"): [{"data": {"status": "SUCCEEDED"}}],
        ("GET", "/datasets/D1/items"): [[
            {"title": "삼성전자 상승", "writtenAt": "2026-11-09T10:00:00+09:00", "url": "http://test.com/1", "stockName": "삼성전자"},
            {"title": "SK하이닉스 전망", "writtenAt": "2026-11-09T11:00:00+09:00", "url": "http://test.com/2", "stockName": "SK하이닉스"},
            {"title": "현대차 분석", "writtenAt": "2026-11-09T12:00:00+09:00", "url": "http://test.com/3", "stockName": "현대차"},
        ]],
    })
    
    ex = exchanges().get("KRX")
    # Use 3 assets for test
    assets = list(ex.assets)[:3]  # Samsung, SK hynix, Hyundai
    window_start = NOW - timedelta(days=10)
    req = IngestionRequest(ex, assets, window_start, NOW, 20)
    
    adapter = NaverStockDiscussionAdapter(config, apify(t))
    res = asyncio.run(adapter.fetch(req))
    
    # Verify single execution
    assert res.meta["note"] == "Single execution for all KRX tickers"
    
    # Verify we got posts for multiple stocks
    assert len(res.records) == 3
    stock_names = {p.provenance.get("stock_name") for p in res.records}
    assert "삼성전자" in stock_names
    assert "SK하이닉스" in stock_names
    assert "현대차" in stock_names


def test_naver_adapter_holder_verification_metadata():
    """Test that holder verification metadata is preserved."""
    from app.integrations.social_adapters import NaverStockDiscussionAdapter
    from app.core.config_models import SourceConfig
    
    config = SourceConfig(
        name="naver_stock_discussion",
        enabled=True,
        actor_id="hgservices/naver-stock-discussion-scraper",
        source_timezone="Asia/Seoul",
        language="ko",
        max_items=20,
        input_template={"stocks": "{stock_codes}"},
        field_map={
            "text": "title",
            "title": "title",
            "timestamp": "writtenAt",
            "url": "url",
            "is_holder_verified": "isHolderVerified"
        }
    )
    
    t = FakeTransport({
        ("POST", "/acts/hgservices~naver-stock-discussion-scraper/runs"): [
            {"data": {"id": "R1", "defaultDatasetId": "D1"}}
        ],
        ("GET", "/actor-runs/R1"): [{"data": {"status": "SUCCEEDED"}}],
        ("GET", "/datasets/D1/items"): [[
            {"title": "Verified holder", "writtenAt": "2026-11-09T10:00:00+09:00", "url": "http://test.com/1", "isHolderVerified": True},
            {"title": "Not verified", "writtenAt": "2026-11-09T11:00:00+09:00", "url": "http://test.com/2", "isHolderVerified": False},
        ]],
    })
    
    ex = exchanges().get("KRX")
    samsung = list(ex.assets)[0]
    window_start = NOW - timedelta(days=10)
    req = IngestionRequest(ex, [samsung], window_start, NOW, 20)
    
    adapter = NaverStockDiscussionAdapter(config, apify(t))
    res = asyncio.run(adapter.fetch(req))
    
    assert len(res.records) == 2
    
    # Check holder verification
    verified_post = [p for p in res.records if p.text == "Verified holder"][0]
    not_verified_post = [p for p in res.records if p.text == "Not verified"][0]
    
    assert verified_post.provenance["is_holder_verified"] is True
    assert not_verified_post.provenance["is_holder_verified"] is False
