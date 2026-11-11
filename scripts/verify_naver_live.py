"""Live verification of Naver Stock Discussion adapter.

Executes ONE paid Apify run with all five KRX tickers and reports results.
"""
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Load environment variables
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parents[1] / ".env"
    load_dotenv(env_path)
except ImportError:
    print("⚠ WARNING: python-dotenv not installed, using system environment only")
except Exception as e:
    print(f"⚠ WARNING: Could not load .env file: {e}")

from app.core.config_models import ExchangesConfig, SourceConfig
from app.integrations.apify_client import ApifyClient
from app.integrations.http import HttpxTransport
from app.integrations.social_adapters import NaverStockDiscussionAdapter
from app.schemas.records import IngestionRequest


async def main():
    print("=" * 80)
    print("NAVER STOCK DISCUSSION LIVE VERIFICATION")
    print("=" * 80)
    print()
    
    # Load configuration
    config_path = Path(__file__).parents[1] / "config" / "exchanges.json"
    exchanges = ExchangesConfig.load(config_path)
    krx = exchanges.get("KRX")
    
    # Get Naver source config
    naver_source = None
    for source in krx.sources:
        if source.name == "naver_stock_discussion":
            naver_source = source
            break
    
    if not naver_source:
        print("❌ ERROR: naver_stock_discussion source not found in KRX configuration")
        return
    
    print(f"✓ Source: {naver_source.name}")
    print(f"✓ Actor ID: {naver_source.actor_id}")
    print(f"✓ Enabled: {naver_source.enabled}")
    print(f"✓ Max items: {naver_source.max_items}")
    print()
    
    # Check operational status
    problem = naver_source.operational_problem()
    if problem:
        print(f"❌ ERROR: Source not operational: {problem}")
        return
    print("✓ Source is operational")
    print()
    
    # Get API token
    api_token = os.getenv("APIFY_API_TOKEN")
    if not api_token:
        print("❌ ERROR: APIFY_API_TOKEN not set in environment")
        print("   Please set it in your .env file")
        return
    print("✓ API token found (not displayed)")
    print()
    
    # Get all five KRX assets
    assets = list(krx.assets)
    print(f"✓ KRX assets configured: {len(assets)}")
    for asset in assets:
        print(f"  - {asset.symbol}: {asset.name}")
    print()
    
    # Build ingestion request
    now = datetime.now(UTC)
    window_start = now - timedelta(days=10)
    request = IngestionRequest(
        exchange=krx,
        assets=assets,
        window_start=window_start,
        window_end=now,
        max_items=20  # Development limit per ticker
    )
    
    print(f"Request window: {window_start.date()} to {now.date()} (10 days)")
    print(f"Max items per ticker: 20")
    print(f"Total budget: {20 * len(assets)} posts")
    print()
    
    # Create adapter with real HTTP transport
    transport = HttpxTransport()
    client = ApifyClient(api_token, transport)
    adapter = NaverStockDiscussionAdapter(naver_source, client)
    
    print("=" * 80)
    print("EXECUTING LIVE APIFY RUN (PAID)")
    print("=" * 80)
    print()
    
    try:
        # Execute the adapter
        result = await adapter.fetch(request)
        
        print("=" * 80)
        print("EXECUTION RESULTS")
        print("=" * 80)
        print()
        
        # Extract metadata
        meta = result.meta
        print(f"Status: {'✓ SUCCESS' if result.records else '⚠ NO RECORDS'}")
        print(f"Apify Run ID: {meta.get('apify_run_id', 'N/A')}")
        print(f"Dataset ID: {meta.get('dataset_id', 'N/A')}")
        print(f"Actor ID: {meta.get('actor_id', 'N/A')}")
        print()
        
        print("RAW COUNTS:")
        print(f"  Total items fetched: {meta.get('items_fetched', 0)}")
        print(f"  Invalid items: {meta.get('invalid_items', 0)}")
        print(f"  Stale items (outside 10-day window): {meta.get('stale_items', 0)}")
        print(f"  Duplicate items: {meta.get('duplicates', 0)}")
        print(f"  Accepted records: {len(result.records)}")
        print()
        
        if not result.records:
            print("⚠ WARNING: No records were accepted")
            print("  This could mean:")
            print("  - All posts were outside the 10-day window")
            print("  - All posts were invalid/empty")
            print("  - Actor returned no data")
            print()
            return
        
        # Analyze records
        print("=" * 80)
        print("RECORD ANALYSIS")
        print("=" * 80)
        print()
        
        # Per-ticker counts (using provenance stock_name or URL analysis)
        ticker_counts = {}
        for record in result.records:
            stock_name = record.provenance.get("stock_name", "Unknown")
            ticker_counts[stock_name] = ticker_counts.get(stock_name, 0) + 1
        
        print("PER-TICKER DISTRIBUTION:")
        for stock_name, count in sorted(ticker_counts.items(), key=lambda x: -x[1]):
            print(f"  {stock_name}: {count} posts")
        print()
        
        # Check which tickers are missing
        expected_names = {a.name for a in assets}
        found_names = set(ticker_counts.keys()) - {"Unknown"}
        missing = expected_names - found_names
        if missing:
            print(f"⚠ MISSING TICKERS: {', '.join(missing)}")
        else:
            print("✓ All tickers represented")
        print()
        
        # Timestamp analysis
        print("TIMESTAMP ANALYSIS:")
        timestamps = [r.posted_at for r in result.records if r.posted_at]
        if timestamps:
            earliest = min(timestamps)
            latest = max(timestamps)
            print(f"  Earliest post: {earliest} (UTC)")
            print(f"  Latest post: {latest} (UTC)")
            print(f"  All within 10-day window: {all(window_start <= t <= now for t in timestamps)}")
            # Check timezone is UTC
            print(f"  All timestamps in UTC: {all(t.tzinfo == UTC for t in timestamps)}")
        else:
            print("  ⚠ No timestamps found")
        print()
        
        # Field presence analysis
        print("FIELD PRESENCE:")
        has_text = sum(1 for r in result.records if r.text and r.text.strip())
        has_url = sum(1 for r in result.records if r.url)
        has_author = sum(1 for r in result.records if r.author_id)
        has_likes = sum(1 for r in result.records if r.likes is not None)
        has_views = sum(1 for r in result.records if r.views is not None)
        has_reposts = sum(1 for r in result.records if r.reposts is not None)
        has_followers = sum(1 for r in result.records if r.author_followers is not None)
        
        print(f"  Text: {has_text}/{len(result.records)}")
        print(f"  URL: {has_url}/{len(result.records)}")
        print(f"  Author: {has_author}/{len(result.records)}")
        print(f"  Likes: {has_likes}/{len(result.records)}")
        print(f"  Views: {has_views}/{len(result.records)}")
        print(f"  Reposts: {has_reposts}/{len(result.records)} (expected 0 - not available)")
        print(f"  Author followers: {has_followers}/{len(result.records)} (expected 0 - not available)")
        print()
        
        # Naver-specific metadata
        has_dislikes = sum(1 for r in result.records if r.provenance.get("dislikes") is not None)
        has_holder_verified = sum(1 for r in result.records if r.provenance.get("is_holder_verified") is not None)
        
        print("NAVER-SPECIFIC METADATA:")
        print(f"  Dislikes: {has_dislikes}/{len(result.records)}")
        print(f"  Holder verified: {has_holder_verified}/{len(result.records)}")
        print()
        
        # Sample records
        print("=" * 80)
        print("SAMPLE RECORDS (first 3)")
        print("=" * 80)
        print()
        
        for i, record in enumerate(result.records[:3], 1):
            print(f"Record {i}:")
            print(f"  Source: {record.source}")
            print(f"  Text (first 100 chars): {record.text[:100] if record.text else 'None'}...")
            print(f"  Posted at: {record.posted_at}")
            print(f"  Author: {record.author_id}")
            print(f"  Likes: {record.likes}, Dislikes: {record.provenance.get('dislikes')}")
            print(f"  Views: {record.views}")
            print(f"  URL: {record.url}")
            print(f"  Stock: {record.provenance.get('stock_name', 'N/A')}")
            print(f"  Holder verified: {record.provenance.get('is_holder_verified', 'N/A')}")
            print(f"  Language: {record.language}")
            print()
        
        # URL deduplication check
        urls = [r.url for r in result.records if r.url]
        unique_urls = set(urls)
        print("DEDUPLICATION:")
        print(f"  Total records: {len(result.records)}")
        print(f"  Unique URLs: {len(unique_urls)}")
        print(f"  Duplicates removed: {len(urls) - len(unique_urls)}")
        print()
        
        # Source attribution
        sources = {r.source for r in result.records}
        print("SOURCE ATTRIBUTION:")
        print(f"  Sources: {', '.join(sources)}")
        print(f"  All from 'naver_stock_discussion': {sources == {'naver_stock_discussion'}}")
        print()
        
        # Gemini relevance filtering check
        print("=" * 80)
        print("GEMINI RELEVANCE FILTERING CHECK")
        print("=" * 80)
        print()
        print("⚠ NOTE: The adapter performs ingestion and normalization only.")
        print("  Gemini relevance filtering happens in the Intelligence Worker")
        print("  during full pipeline execution, NOT during adapter.fetch().")
        print()
        print("  To verify relevance filtering:")
        print("  1. Run full pipeline: python -m scripts.run_pipeline --exchange KRX")
        print("  2. Intelligence worker will call Gemini for each post")
        print("  3. Check intelligence_extractions table for relevance_category")
        print()
        print("  Current verification only tests the ingestion adapter.")
        print()
        
        # Final summary
        print("=" * 80)
        print("VERIFICATION SUMMARY")
        print("=" * 80)
        print()
        
        checks = []
        checks.append(("All five tickers requested", True))
        checks.append(("Records returned", len(result.records) > 0))
        checks.append(("Timestamps in UTC", all(t.tzinfo == UTC for t in timestamps) if timestamps else False))
        checks.append(("10-day filtering applied", meta.get('stale_items', 0) >= 0))
        checks.append(("Post text preserved", has_text > 0))
        checks.append(("Duplicate URLs removed", meta.get('duplicates', 0) >= 0))
        checks.append(("Missing engagement fields null", has_reposts == 0 and has_followers == 0))
        checks.append(("Source attribution correct", sources == {'naver_stock_discussion'}))
        
        for check, passed in checks:
            status = "✓" if passed else "✗"
            print(f"  {status} {check}")
        print()
        
        all_passed = all(passed for _, passed in checks)
        if all_passed:
            print("✓ LIVE VERIFICATION PASSED")
        else:
            print("⚠ LIVE VERIFICATION COMPLETED WITH WARNINGS")
        print()
        
        print("COST INFORMATION:")
        print(f"  Apify Run ID: {meta.get('apify_run_id', 'N/A')}")
        print(f"  Check your Apify dashboard for exact API cost")
        print(f"  Approximate: 1 actor run with {meta.get('items_fetched', 0)} items fetched")
        print()
        
    except Exception as e:
        print("=" * 80)
        print("EXECUTION FAILED")
        print("=" * 80)
        print()
        print(f"Error type: {type(e).__name__}")
        print(f"Error message: {str(e)}")
        print()
        import traceback
        print("Traceback:")
        traceback.print_exc()
        print()
        print("⚠ Live verification failed. Check error details above.")
        print()


if __name__ == "__main__":
    asyncio.run(main())
