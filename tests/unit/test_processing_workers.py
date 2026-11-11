import asyncio
from datetime import datetime, timedelta

from app.schemas.records import NewsArticle, SocialPost
from app.services.ticker_service import TickerService
from app.workers.processing import (
    CleaningWorker,
    DeduplicationWorker,
    EngagementNormalizationWorker,
    TickerMappingWorker,
    TimestampNormalizationWorker,
    canonical_url,
)
from tests.helpers import NOW, exchanges

run = lambda w, p: asyncio.run(w.execute(p)).output


def post(i="1", text="Tencent 0700 up", ts=NOW - timedelta(days=1), **kw):
    return SocialPost(source="lihkg", source_post_id=i, text=text, posted_at=ts, author_id=kw.pop("author_id", "u"), **kw)


def test_cleaning_strips_html_and_zero_width_and_drops_empty():
    out = run(CleaningWorker(), [post(text="<b>騰訊</b>&amp; \u200b  good "), post("2", text=" ")])
    assert [p.text for p in out] == ["騰訊 & good"] and out[0].text_raw.startswith("<b>")


def test_timestamps_naive_to_utc_drop_missing_and_future_and_flag_undated_news():
    w = TimestampNormalizationWorker(NOW - timedelta(days=10), NOW)
    news_no_date = NewsArticle("exa", "n1", "http://x/a", "t", "t", None, retrieved_at=NOW)
    out = run(w, [post("a", ts=datetime(2026, 11, 9, 1, 0)), post("b", ts=None), post("c", ts=NOW + timedelta(days=2)),
                  post("d", ts=NOW - timedelta(days=30)), news_no_date])
    ids = {r.record_id: r for r in out}
    assert set(ids) == {"lihkg:a", "exa:n1"}
    assert ids["lihkg:a"].posted_at.tzinfo is not None
    assert ids["exa:n1"].usable_for_features is False        # retrieval time is not used as publication time


def test_dedup_keeps_earliest_and_provenance():
    a = post("1", "Same text", NOW - timedelta(hours=5))
    b = post("2", "Same text", NOW - timedelta(hours=1))
    c = post("3", "Different", NOW - timedelta(hours=2))
    out = run(DeduplicationWorker(), [b, a, c])
    kept = {r.source_post_id: r for r in out}
    assert set(kept) == {"1", "3"} and kept["1"].provenance["duplicate_ids"] == ["lihkg:2"] and kept["1"].duplicate_count == 1


def test_news_dedup_on_canonical_url_merges_assets():
    assert canonical_url("https://WWW.Site.com/a/?utm_source=x&id=1#frag") == "https://site.com/a?id=1"
    mk = lambda i, s: NewsArticle("exa", i, "https://site.com/a?utm_x=1", "T", "T", NOW, asset_symbols=[s])
    out = run(DeduplicationWorker(), [mk("1", "0700.HK"), mk("2", "9988.HK")])
    assert len(out) == 1 and set(out[0].asset_symbols) == {"0700.HK", "9988.HK"}


def test_ticker_mapping_multi_script_and_unmapped_dropped():
    svc = TickerService(exchanges().get("HKEX").assets)
    out = run(TickerMappingWorker(svc), [post("1", "騰訊 and Alibaba both up"), post("2", "nothing relevant, 10700 shares"),
                                          post("3", "MAIAMI beach")])
    assert [(r.source_post_id, r.asset_symbols) for r in out] == [("1", ["0700.HK", "9988.HK"])]


def test_ticker_mapping_korean_and_boundaries():
    svc = TickerService(exchanges().get("KRX").assets)
    assert svc.symbols("삼성전자는 오늘 상승, 005930 강세") == ["005930.KS"]
    assert svc.symbols("price 1005930 units") == []


def test_engagement_weights_missing_flag():
    out = run(EngagementNormalizationWorker(), [post("1", likes=100, reposts=5), post("2")])
    by = {r.source_post_id: r for r in out}
    assert by["1"].engagement_weight > 1 and by["1"].engagement_missing is False
    assert by["2"].engagement_weight == 1.0 and by["2"].engagement_missing is True
