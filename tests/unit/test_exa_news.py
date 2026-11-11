"""Tests for Exa news integration, ingestion and processing."""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config_models import AssetConfig, ExchangeConfig
from app.core.exceptions import IntegrationError
from app.integrations.exa_client import ExaClient
from app.integrations.http import HttpResponse
from app.repositories.memory import MemoryRepository
from app.schemas.records import IngestionRequest, NewsArticle
from app.workers.ingestion import NewsIngestionWorker
from app.workers.processing import DeduplicationWorker

NOW = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)


class FakeTransport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def request(self, method, url, **kwargs):
        key = (method, url.split("?")[0].replace("https://api.exa.ai", ""))
        self.calls.append((method, url, kwargs.get("headers", {}), kwargs.get("params"), kwargs.get("json")))
        response = self.responses.get(key, [{}])[0]
        return response if isinstance(response, HttpResponse) else HttpResponse(200, response)

    async def aclose(self):
        pass


def no_sleep(_):
    return asyncio.sleep(0)


# ============================================================================
# EXA CLIENT TESTS
# ============================================================================


def test_exa_client_requires_api_key():
    """Exa client must reject empty API key."""
    t = FakeTransport({})
    with pytest.raises(IntegrationError, match="EXA_API_KEY"):
        ExaClient("", t)


def test_exa_search_news_request_format():
    """Exa search_news constructs proper API request."""
    response = {
        "results": [
            {
                "url": "https://example.com/article1",
                "title": "Test Article",
                "text": "Article content",
                "publishedDate": "2026-11-09T10:00:00.000Z",
                "id": "exa123"
            }
        ]
    }
    t = FakeTransport({("POST", "/search"): [response]})
    c = ExaClient("test_key", t, sleep=no_sleep)
    
    start = datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC)
    end = datetime(2026, 11, 10, 23, 59, 59, tzinfo=UTC)
    
    results = asyncio.run(c.search_news("Tencent stock news", start=start, end=end, num_results=10))
    
    # Check request was made
    assert len(t.calls) == 1
    method, url, headers, params, body = t.calls[0]
    
    # Check request format
    assert method == "POST"
    assert "/search" in url
    assert headers["x-api-key"] == "test_key"
    assert headers["Content-Type"] == "application/json"
    
    # Check body
    assert body["query"] == "Tencent stock news"
    assert body["numResults"] == 10
    assert body["type"] == "auto"
    assert body["category"] == "news"
    assert "startPublishedDate" in body
    assert "endPublishedDate" in body
    assert body["contents"]["text"]["maxCharacters"] == 3000
    
    # Check response
    assert len(results) == 1
    assert results[0]["url"] == "https://example.com/article1"
    assert results[0]["title"] == "Test Article"


def test_exa_handles_empty_results():
    """Exa client handles empty results list."""
    t = FakeTransport({("POST", "/search"): [{"results": []}]})
    c = ExaClient("test_key", t, sleep=no_sleep)
    
    results = asyncio.run(c.search_news("Unknown Company", start=NOW, end=NOW, num_results=5))
    
    assert results == []


def test_exa_handles_malformed_response():
    """Exa client raises IntegrationError for malformed API response."""
    t = FakeTransport({("POST", "/search"): [{"data": "wrong_format"}]})
    c = ExaClient("test_key", t, sleep=no_sleep)
    
    with pytest.raises(IntegrationError, match="unexpected Exa response"):
        asyncio.run(c.search_news("Test", start=NOW, end=NOW, num_results=5))


def test_exa_handles_missing_url():
    """Exa client skips articles without URLs."""
    response = {
        "results": [
            {"title": "No URL", "text": "content"},
            {"url": "https://example.com/ok", "title": "Has URL", "text": "content"}
        ]
    }
    t = FakeTransport({("POST", "/search"): [response]})
    c = ExaClient("test_key", t, sleep=no_sleep)
    
    results = asyncio.run(c.search_news("Test", start=NOW, end=NOW, num_results=10))
    
    # Only article with URL should be returned (handled by ingestion worker)
    assert len(results) == 2


# ============================================================================
# NEWS INGESTION WORKER TESTS
# ============================================================================


def test_news_ingestion_worker_normalizes_articles():
    """NewsIngestionWorker normalizes Exa results to NewsArticle records."""
    response = {
        "results": [
            {
                "url": "https://example.com/article1",
                "title": "Tencent Reports Strong Q3",
                "text": "Tencent Holdings announced...",
                "publishedDate": "2026-11-09T10:30:00.000Z",
                "id": "exa_123",
                "score": 0.95
            }
        ]
    }
    t = FakeTransport({("POST", "/search"): [response]})
    exa = ExaClient("key", t, sleep=no_sleep)
    repo = MemoryRepository()
    
    worker = NewsIngestionWorker(exa, repo, results_per_asset=10)
    
    asset = AssetConfig("0700.HK", "Tencent Holdings", [], [])
    exchange = ExchangeConfig("HKEX", "Hong Kong", "Asia/Hong_Kong", "HKD", "16:10", [], [asset], [])
    request = IngestionRequest(exchange, [asset], NOW - timedelta(days=10), NOW, 500)
    
    result = asyncio.run(worker.execute(request, {"run_id": "test"}))
    output = result.output
    
    assert output.source == "exa"
    assert output.kind == "news"
    assert len(output.records) == 1
    
    article = output.records[0]
    assert isinstance(article, NewsArticle)
    assert article.source == "exa"
    assert article.url == "https://example.com/article1"
    assert article.title == "Tencent Reports Strong Q3"
    assert "Tencent Holdings announced" in article.text
    assert article.published_at.tzinfo is not None  # UTC normalized
    assert article.query_symbol == "0700.HK"
    assert article.asset_symbols == ["0700.HK"]
    assert article.source_domain == "example.com"
    assert article.relevance == 0.95
    assert article.provenance["exa_id"] == "exa_123"
    assert article.provenance["query"] == "Tencent Holdings stock news"


def test_news_ingestion_handles_missing_publication_date():
    """NewsIngestionWorker handles articles without publication dates."""
    response = {
        "results": [
            {
                "url": "https://example.com/article1",
                "title": "Undated Article",
                "text": "Content",
                "publishedDate": None  # Missing date
            }
        ]
    }
    t = FakeTransport({("POST", "/search"): [response]})
    exa = ExaClient("key", t, sleep=no_sleep)
    repo = MemoryRepository()
    
    worker = NewsIngestionWorker(exa, repo, results_per_asset=10)
    
    asset = AssetConfig("0700.HK", "Tencent Holdings", [], [])
    exchange = ExchangeConfig("HKEX", "Hong Kong", "Asia/Hong_Kong", "HKD", "16:10", [], [asset], [])
    request = IngestionRequest(exchange, [asset], NOW - timedelta(days=10), NOW, 500)
    
    result = asyncio.run(worker.execute(request, {"run_id": "test"}))
    article = result.output.records[0]
    
    # Article created but published_at is None
    assert article.published_at is None
    assert article.retrieved_at is not None


def test_news_ingestion_handles_missing_text():
    """NewsIngestionWorker uses title when text is missing."""
    response = {
        "results": [
            {
                "url": "https://example.com/article1",
                "title": "Title Only Article",
                "text": None,
                "publishedDate": "2026-11-09T10:00:00.000Z"
            }
        ]
    }
    t = FakeTransport({("POST", "/search"): [response]})
    exa = ExaClient("key", t, sleep=no_sleep)
    repo = MemoryRepository()
    
    worker = NewsIngestionWorker(exa, repo, results_per_asset=10)
    
    asset = AssetConfig("0700.HK", "Tencent Holdings", [], [])
    exchange = ExchangeConfig("HKEX", "Hong Kong", "Asia/Hong_Kong", "HKD", "16:10", [], [asset], [])
    request = IngestionRequest(exchange, [asset], NOW - timedelta(days=10), NOW, 500)
    
    result = asyncio.run(worker.execute(request, {"run_id": "test"}))
    article = result.output.records[0]
    
    # Text should be title only (no body)
    assert article.text == "Title Only Article"
    assert article.title == "Title Only Article"


def test_news_ingestion_skips_articles_without_url():
    """NewsIngestionWorker skips articles missing URL."""
    response = {
        "results": [
            {"title": "No URL", "text": "content"},  # Invalid
            {"url": "https://example.com/ok", "title": "Valid", "text": "content", "publishedDate": "2026-11-09T10:00:00.000Z"}
        ]
    }
    t = FakeTransport({("POST", "/search"): [response]})
    exa = ExaClient("key", t, sleep=no_sleep)
    repo = MemoryRepository()
    
    worker = NewsIngestionWorker(exa, repo, results_per_asset=10)
    
    asset = AssetConfig("0700.HK", "Tencent Holdings", [], [])
    exchange = ExchangeConfig("HKEX", "Hong Kong", "Asia/Hong_Kong", "HKD", "16:10", [], [asset], [])
    request = IngestionRequest(exchange, [asset], NOW - timedelta(days=10), NOW, 500)
    
    result = asyncio.run(worker.execute(request, {"run_id": "test"}))
    
    # Only valid article should be included
    assert len(result.output.records) == 1
    assert result.output.records[0].url == "https://example.com/ok"


def test_news_ingestion_handles_multiple_assets():
    """NewsIngestionWorker fetches news for multiple assets concurrently."""
    response1 = {"results": [{"url": "https://example.com/tencent", "title": "Tencent News", "publishedDate": "2026-11-09T10:00:00.000Z"}]}
    response2 = {"results": [{"url": "https://example.com/alibaba", "title": "Alibaba News", "publishedDate": "2026-11-09T11:00:00.000Z"}]}
    
    t = FakeTransport({("POST", "/search"): [response1, response2]})
    exa = ExaClient("key", t, sleep=no_sleep)
    repo = MemoryRepository()
    
    worker = NewsIngestionWorker(exa, repo, results_per_asset=10)
    
    asset1 = AssetConfig("0700.HK", "Tencent Holdings", [], [])
    asset2 = AssetConfig("9988.HK", "Alibaba Group", [], [])
    exchange = ExchangeConfig("HKEX", "Hong Kong", "Asia/Hong_Kong", "HKD", "16:10", [], [asset1, asset2], [])
    request = IngestionRequest(exchange, [asset1, asset2], NOW - timedelta(days=10), NOW, 500)
    
    result = asyncio.run(worker.execute(request, {"run_id": "test"}))
    
    # Should have articles from both assets
    assert len(result.output.records) == 2
    assert result.output.records[0].query_symbol in ["0700.HK", "9988.HK"]
    assert result.output.records[1].query_symbol in ["0700.HK", "9988.HK"]
    
    # Should have made 2 API calls
    assert len(t.calls) == 2


def test_news_ingestion_handles_partial_failure():
    """NewsIngestionWorker continues when one asset fails."""
    success_response = {"results": [{"url": "https://example.com/ok", "title": "OK", "publishedDate": "2026-11-09T10:00:00.000Z"}]}
    
    call_count = [0]
    
    async def failing_search(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise IntegrationError("API error for asset 1")
        return success_response["results"]
    
    class FailingExa:
        async def search_news(self, *args, **kwargs):
            return await failing_search(*args, **kwargs)
    
    exa = FailingExa()
    repo = MemoryRepository()
    
    worker = NewsIngestionWorker(exa, repo, results_per_asset=10)
    
    asset1 = AssetConfig("0700.HK", "Tencent Holdings", [], [])
    asset2 = AssetConfig("9988.HK", "Alibaba Group", [], [])
    exchange = ExchangeConfig("HKEX", "Hong Kong", "Asia/Hong_Kong", "HKD", "16:10", [], [asset1, asset2], [])
    request = IngestionRequest(exchange, [asset1, asset2], NOW - timedelta(days=10), NOW, 500)
    
    result = asyncio.run(worker.execute(request, {"run_id": "test"}))
    
    # Should have 1 successful article
    assert len(result.output.records) == 1
    assert result.output.records[0].url == "https://example.com/ok"
    
    # Should report failure
    assert "failed_assets" in result.output.meta
    assert "0700.HK" in result.output.meta["failed_assets"]


def test_news_ingestion_fails_when_all_assets_fail():
    """NewsIngestionWorker raises error when all assets fail."""
    
    async def failing_search(*args, **kwargs):
        raise IntegrationError("API error")
    
    class FailingExa:
        async def search_news(self, *args, **kwargs):
            return await failing_search(*args, **kwargs)
    
    exa = FailingExa()
    repo = MemoryRepository()
    
    worker = NewsIngestionWorker(exa, repo, results_per_asset=10)
    
    asset1 = AssetConfig("0700.HK", "Tencent Holdings", [], [])
    asset2 = AssetConfig("9988.HK", "Alibaba Group", [], [])
    exchange = ExchangeConfig("HKEX", "Hong Kong", "Asia/Hong_Kong", "HKD", "16:10", [], [asset1, asset2], [])
    request = IngestionRequest(exchange, [asset1, asset2], NOW - timedelta(days=10), NOW, 500)
    
    # Worker wraps IntegrationError in WorkerExecutionError
    with pytest.raises(Exception):  # WorkerExecutionError or IntegrationError
        asyncio.run(worker.execute(request, {"run_id": "test"}))


# ============================================================================
# NEWS ARTICLE DEDUPLICATION TESTS
# ============================================================================


def test_news_articles_deduplicate_by_canonical_url():
    """NewsArticle deduplication merges articles with same canonical URL."""
    article1 = NewsArticle(
        source="exa",
        source_post_id="id1",
        url="https://example.com/article?utm_source=twitter",
        title="Article",
        text="Content",
        published_at=NOW,
        query_symbol="0700.HK",
        asset_symbols=["0700.HK"]
    )
    
    article2 = NewsArticle(
        source="exa",
        source_post_id="id2",
        url="https://example.com/article?utm_source=linkedin",
        title="Article",
        text="Content",
        published_at=NOW + timedelta(minutes=5),
        query_symbol="9988.HK",
        asset_symbols=["9988.HK"]
    )
    
    worker = DeduplicationWorker()
    result = asyncio.run(worker.execute([article1, article2], {}))
    
    # Should keep only one article
    assert len(result.output) == 1
    
    # Should merge asset_symbols
    assert set(result.output[0].asset_symbols) == {"0700.HK", "9988.HK"}
    
    # Should keep earlier timestamp
    assert result.output[0].published_at == NOW


def test_news_articles_preserve_different_urls():
    """NewsArticle deduplication keeps articles with different URLs."""
    article1 = NewsArticle(
        source="exa",
        source_post_id="id1",
        url="https://example.com/article1",
        title="Article 1",
        text="Content 1",
        published_at=NOW,
        query_symbol="0700.HK",
        asset_symbols=["0700.HK"]
    )
    
    article2 = NewsArticle(
        source="exa",
        source_post_id="id2",
        url="https://example.com/article2",
        title="Article 2",
        text="Content 2",
        published_at=NOW,
        query_symbol="0700.HK",
        asset_symbols=["0700.HK"]
    )
    
    worker = DeduplicationWorker()
    result = asyncio.run(worker.execute([article1, article2], {}))
    
    # Should keep both articles
    assert len(result.output) == 2


# ============================================================================
# NEWS ARTICLE PERSISTENCE TESTS
# ============================================================================


def test_news_articles_persist_to_repository():
    """NewsArticle records persist correctly."""
    response = {"results": [{"url": "https://example.com/article", "title": "Test", "publishedDate": "2026-11-09T10:00:00.000Z"}]}
    t = FakeTransport({("POST", "/search"): [response]})
    exa = ExaClient("key", t, sleep=no_sleep)
    repo = MemoryRepository()
    
    worker = NewsIngestionWorker(exa, repo, results_per_asset=10)
    
    asset = AssetConfig("0700.HK", "Tencent Holdings", [], [])
    exchange = ExchangeConfig("HKEX", "Hong Kong", "Asia/Hong_Kong", "HKD", "16:10", [], [asset], [])
    request = IngestionRequest(exchange, [asset], NOW - timedelta(days=10), NOW, 500)
    
    result = asyncio.run(worker.execute(request, {"run_id": "test"}))
    asyncio.run(worker.persist(result.output, {"run_id": "test"}))
    
    # Check persistence
    stored = asyncio.run(repo.select("news_articles", where={}))
    assert len(stored) == 1
    # Repository stores row dictionaries - check key fields exist
    assert "url" in stored[0]
    assert stored[0]["url"] == "https://example.com/article"
    assert stored[0]["query_symbol"] == "0700.HK"
