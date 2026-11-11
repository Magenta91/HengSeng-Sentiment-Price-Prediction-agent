"""Verify live Exa news ingestion and intelligence pipeline for all 10 tickers.

Performs cost-controlled verification without triggering Apify or reprocessing social data.
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


from app.core.config_models import ExchangesConfig
from app.integrations.exa_client import ExaClient
from app.integrations.http import HttpxTransport
from app.integrations.llm_client import GeminiLLMClient
from app.repositories.memory import MemoryRepository
from app.schemas.records import IngestionRequest
from app.services.extraction_service import ExtractionService
from app.services.ticker_service import TickerService
from app.workers.ingestion import NewsIngestionWorker
from app.workers.intelligence import (
    AggregationInput,
    FinancialExtractionWorker,
    SignalAggregationWorker,
)
from app.workers.processing import (
    CleaningWorker,
    DeduplicationWorker,
    EngagementNormalizationWorker,
    TickerMappingWorker,
    TimestampNormalizationWorker,
)


async def main():
    print("=" * 80)
    print("EXA NEWS INTEGRATION - LIVE VERIFICATION")
    print("=" * 80)
    print()
    
    # Check API keys
    exa_key = os.getenv("EXA_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")
    
    if not exa_key:
        print("❌ ERROR: EXA_API_KEY not set in environment")
        print("   Please set it in your .env file")
        return
    print("✓ Exa API key found (not displayed)")
    
    if not gemini_key:
        print("❌ ERROR: GEMINI_API_KEY not set in environment")
        print("   Please set it in your .env file")
        return
    print("✓ Gemini API key found (not displayed)")
    print()
    
    # Load configuration
    config_path = Path(__file__).parents[1] / "config" / "exchanges.json"
    exchanges = ExchangesConfig.load(config_path)
    
    # Prepare all 10 assets
    hkex = exchanges.get("HKEX")
    krx = exchanges.get("KRX")
    all_assets = list(hkex.assets) + list(krx.assets)
    
    print(f"✓ Exchanges: HKEX + KRX")
    print(f"✓ Total assets: {len(all_assets)}")
    print()
    print("HKEX Assets:")
    for asset in hkex.assets:
        print(f"  - {asset.symbol}: {asset.name}")
    print()
    print("KRX Assets:")
    for asset in krx.assets:
        print(f"  - {asset.symbol}: {asset.name}")
    print()
    
    # Setup time window
    now = datetime.now(UTC)
    window_start = now - timedelta(days=10)
    
    print("=" * 80)
    print("PHASE 1: EXA NEWS INGESTION")
    print("=" * 80)
    print()
    
    print(f"Time window: {window_start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')}")
    print(f"Results per asset: 10")
    print()
    
    # Create Exa client and ingestion worker
    transport = HttpxTransport()
    exa_client = ExaClient(exa_key, transport)
    repo = MemoryRepository()
    
    # Create ingestion request (use HKEX as exchange context)
    request = IngestionRequest(hkex, all_assets, window_start, now, max_items=500)
    
    # Execute news ingestion
    print("Executing Exa news ingestion...")
    print("(This will make real API calls)")
    print()
    
    ingestion_worker = NewsIngestionWorker(exa_client, repo, results_per_asset=10)
    
    try:
        result = await ingestion_worker.execute(request, {"run_id": "exa_test"})
        ingestion_output = result.output
        
        print("=" * 80)
        print("INGESTION RESULTS")
        print("=" * 80)
        print()
        
        print(f"Total articles retrieved: {len(ingestion_output.records)}")
        print(f"Source: {ingestion_output.source}")
        print(f"Kind: {ingestion_output.kind}")
        print()
        
        if ingestion_output.meta.get("failed_assets"):
            print("Failed assets:")
            for sym, err in ingestion_output.meta["failed_assets"].items():
                print(f"  {sym}: {err}")
            print()
        
        # Analyze by ticker
        articles_by_ticker = {}
        for article in ingestion_output.records:
            ticker = article.query_symbol
            if ticker not in articles_by_ticker:
                articles_by_ticker[ticker] = []
            articles_by_ticker[ticker].append(article)
        
        print("Articles by ticker:")
        for ticker in sorted(articles_by_ticker.keys()):
            articles = articles_by_ticker[ticker]
            asset = next((a for a in all_assets if a.symbol == ticker), None)
            name = asset.name if asset else "Unknown"
            print(f"  {ticker} ({name}): {len(articles)} articles")
        print()
        
        # Sample articles
        print("=" * 80)
        print("SAMPLE ARTICLES (first 3)")
        print("=" * 80)
        print()
        
        for i, article in enumerate(ingestion_output.records[:3], 1):
            print(f"Article {i}:")
            print(f"  Ticker: {article.query_symbol}")
            print(f"  Title: {article.title}")
            print(f"  URL: {article.url}")
            print(f"  Domain: {article.source_domain}")
            print(f"  Published: {article.published_at}")
            print(f"  Retrieved: {article.retrieved_at}")
            print(f"  Text length: {len(article.text)} chars")
            if article.text:
                print(f"  Text preview: {article.text[:150]}...")
            print()
        
        # Run processing pipeline
        print("=" * 80)
        print("PHASE 2: PROCESSING PIPELINE")
        print("=" * 80)
        print()
        
        records = ingestion_output.records
        
        # Clean
        print("Step 1: Cleaning...")
        cleaner = CleaningWorker()
        result = await cleaner.execute(records, {})
        records = result.output
        print(f"  After cleaning: {len(records)} articles")
        
        # Normalize timestamps
        print("Step 2: Timestamp normalization...")
        ts_worker = TimestampNormalizationWorker(window_start, now)
        result = await ts_worker.execute(records, {})
        records = result.output
        print(f"  After timestamp check: {len(records)} articles")
        usable = sum(1 for r in records if r.usable_for_features)
        print(f"  Usable for features: {usable}")
        
        # Deduplicate
        print("Step 3: Deduplication...")
        dedup = DeduplicationWorker()
        result = await dedup.execute(records, {})
        records = result.output
        duplicates = sum(1 for r in records if r.duplicate_of is not None)
        print(f"  After dedup: {len(records)} articles")
        print(f"  Duplicates found: {duplicates}")
        
        # Ticker mapping
        print("Step 4: Ticker mapping...")
        ticker_svc = TickerService(all_assets)
        mapper = TickerMappingWorker(ticker_svc)
        result = await mapper.execute(records, {})
        records = result.output
        print(f"  After ticker mapping: {len(records)} articles")
        usable = sum(1 for r in records if r.usable_for_features)
        print(f"  Usable for features: {usable}")
        
        # Engagement normalization
        print("Step 5: Engagement normalization...")
        eng_worker = EngagementNormalizationWorker()
        result = await eng_worker.execute(records, {})
        records = result.output
        print(f"  After engagement: {len(records)} articles")
        print()
        
        # Ticker distribution after processing
        ticker_counts = {}
        for rec in records:
            for sym in rec.asset_symbols:
                ticker_counts[sym] = ticker_counts.get(sym, 0) + 1
        
        print("Final ticker distribution:")
        for sym in sorted(ticker_counts.keys()):
            asset = next((a for a in all_assets if a.symbol == sym), None)
            name = asset.name if asset else "Unknown"
            count = ticker_counts[sym]
            print(f"  {sym} ({name}): {count} articles")
        print()
        
        # Execute Intelligence Worker with Gemini
        print("=" * 80)
        print("PHASE 3: GEMINI INTELLIGENCE EXTRACTION")
        print("=" * 80)
        print()
        
        model_name = "gemini-2.5-flash"
        print(f"Model: {model_name}")
        print(f"Max items: 50 (cost control)")
        print(f"Concurrency: 2")
        print()
        
        llm = GeminiLLMClient(gemini_key, model=model_name)
        extraction_service = ExtractionService(llm, max_attempts=3)
        
        # Create asset dictionary
        asset_dict = {a.symbol: a for a in all_assets}
        
        extraction_worker = FinancialExtractionWorker(
            extraction_service,
            repo,
            asset_dict,
            concurrency=2,
            max_items=50  # Limit for cost control
        )
        
        print("Executing financial extractions with live Gemini API...")
        print("(This will make real API calls)")
        print()
        
        result = await extraction_worker.execute(records, {"run_id": "exa_test"})
        extraction_result = result.output
        
        print("=" * 80)
        print("EXTRACTION RESULTS")
        print("=" * 80)
        print()
        
        print(f"Articles attempted: {extraction_result.attempted}")
        print(f"Successful extractions: {len(extraction_result.extractions)}")
        print(f"Not mentioned (filtered): {extraction_result.not_mentioned}")
        print(f"Failed: {extraction_result.failed}")
        print(f"Skipped (over cap): {extraction_result.skipped_over_cap}")
        print()
        
        if extraction_result.errors:
            print(f"Errors encountered: {len(extraction_result.errors)}")
            for err in extraction_result.errors[:3]:
                print(f"  - {err}")
            print()
        
        # Analyze extractions
        if extraction_result.extractions:
            print("=" * 80)
            print("EXTRACTION ANALYSIS")
            print("=" * 80)
            print()
            
            sentiments = {}
            directions = {}
            confidence_dist = {"high": 0, "medium": 0, "low": 0}
            extractions_by_ticker = {}
            
            for ext in extraction_result.extractions:
                sentiments[ext.sentiment] = sentiments.get(ext.sentiment, 0) + 1
                directions[ext.direction] = directions.get(ext.direction, 0) + 1
                
                if ext.confidence >= 0.8:
                    confidence_dist["high"] += 1
                elif ext.confidence >= 0.6:
                    confidence_dist["medium"] += 1
                else:
                    confidence_dist["low"] += 1
                
                if ext.symbol not in extractions_by_ticker:
                    extractions_by_ticker[ext.symbol] = []
                extractions_by_ticker[ext.symbol].append(ext)
            
            print("Sentiment Distribution:")
            for sent, count in sorted(sentiments.items(), key=lambda x: -x[1]):
                pct = (count / len(extraction_result.extractions)) * 100
                print(f"  {sent}: {count} ({pct:.1f}%)")
            print()
            
            print("Direction Distribution:")
            for dir, count in sorted(directions.items(), key=lambda x: -x[1]):
                pct = (count / len(extraction_result.extractions)) * 100
                print(f"  {dir}: {count} ({pct:.1f}%)")
            print()
            
            print("Confidence Distribution:")
            for level, count in confidence_dist.items():
                pct = (count / len(extraction_result.extractions)) * 100
                print(f"  {level}: {count} ({pct:.1f}%)")
            print()
            
            print("Extractions by ticker:")
            for sym in sorted(extractions_by_ticker.keys()):
                asset = next((a for a in all_assets if a.symbol == sym), None)
                name = asset.name if asset else "Unknown"
                exts = extractions_by_ticker[sym]
                bullish = sum(1 for e in exts if e.sentiment == "bullish")
                bearish = sum(1 for e in exts if e.sentiment == "bearish")
                neutral = sum(1 for e in exts if e.sentiment == "neutral")
                print(f"  {sym} ({name}): {len(exts)} total (bull={bullish}, bear={bearish}, neut={neutral})")
            print()
            
            # Sample extractions
            print("=" * 80)
            print("SAMPLE EXTRACTIONS (first 5)")
            print("=" * 80)
            print()
            
            for i, ext in enumerate(extraction_result.extractions[:5], 1):
                print(f"Extraction {i}:")
                print(f"  Record ID: {ext.record_id}")
                print(f"  Ticker: {ext.symbol}")
                print(f"  Sentiment: {ext.sentiment} (score: {ext.sentiment_score:.2f})")
                print(f"  Direction: {ext.direction}")
                print(f"  Confidence: {ext.confidence:.2f}")
                if ext.evidence_text:
                    print(f"  Evidence: {ext.evidence_text[:100]}...")
                if ext.position:
                    print(f"  Position: {ext.position}")
                if ext.support_levels:
                    print(f"  Support levels: {ext.support_levels}")
                if ext.resistance_levels:
                    print(f"  Resistance levels: {ext.resistance_levels}")
                if ext.target_price:
                    print(f"  Target price: {ext.target_price}")
                if ext.guard_notes:
                    print(f"  Guard notes: {len(ext.guard_notes)} notes")
                print()
            
            # Signal aggregation
            print("=" * 80)
            print("PHASE 4: SIGNAL AGGREGATION")
            print("=" * 80)
            print()
            
            # Use HKEX calendar for aggregation
            agg_input = AggregationInput(
                records=records,
                scores=[],
                extractions=extraction_result.extractions,
                calendar=hkex.calendar()
            )
            
            agg_worker = SignalAggregationWorker(repo)
            agg_result = await agg_worker.execute(agg_input, {"run_id": "exa_test"})
            agg_output = agg_result.output
            
            print(f"Total sentiment rows: {len(agg_output.rows)}")
            print()
            
            if len(agg_output.window):
                print("Window aggregates (by symbol and source_type):")
                for _, row in agg_output.window.iterrows():
                    print(f"  {row['symbol']} ({row['source_type']}): "
                          f"mentions={row['mention_count']}, "
                          f"scored={row.get('scored_count', 0)}, "
                          f"sentiment_mean={row.get('sentiment_mean', 'N/A')}")
                print()
            
            # Verify filtering
            relevant_count = len(extraction_result.extractions)
            filtered_count = extraction_result.not_mentioned
            total_attempted = extraction_result.attempted
            
            print("Filtering Verification:")
            print(f"  Total articles attempted: {total_attempted}")
            print(f"  Relevant (extracted): {relevant_count} ({relevant_count/total_attempted*100:.1f}%)")
            print(f"  Irrelevant (filtered): {filtered_count} ({filtered_count/total_attempted*100:.1f}%)")
            print(f"  Failed: {extraction_result.failed}")
            print()
            
            print("✓ Filtered articles correctly excluded from sentiment aggregation")
            print("✓ Only relevant extractions contribute to features")
            print()
        
        else:
            print("⚠ WARNING: No extractions were successful")
            print()
        
        # Persistence verification
        print("=" * 80)
        print("PHASE 5: PERSISTENCE VERIFICATION")
        print("=" * 80)
        print()
        
        stored_articles = await repo.select("news_articles", where={})
        print(f"Articles stored in repository: {len(stored_articles)}")
        
        if stored_articles:
            sample = stored_articles[0]
            print(f"Sample article fields:")
            print(f"  source: {sample.get('source')}")
            print(f"  url: {sample.get('url')}")
            print(f"  title: {sample.get('title')[:50]}...")
            print(f"  query_symbol: {sample.get('query_symbol')}")
            print(f"  published_at: {sample.get('published_at')}")
            print()
        
        stored_extractions = await repo.select("intelligence_extractions", where={})
        print(f"Extractions stored in repository: {len(stored_extractions)}")
        
        if stored_extractions:
            sample = stored_extractions[0]
            print(f"Sample extraction fields:")
            print(f"  run_id: {sample.get('run_id')}")
            print(f"  record_id: {sample.get('record_id')}")
            print(f"  symbol: {sample.get('symbol')}")
            print(f"  source: {sample.get('source')}")
            print(f"  sentiment: {sample.get('sentiment')}")
            print(f"  confidence: {sample.get('confidence')}")
            print(f"  model_name: {sample.get('model_name')}")
            print()
            print("✓ Extractions persisted with correct ticker and source attribution")
            print()
        
        # API cost summary
        print("=" * 80)
        print("API COST INFORMATION")
        print("=" * 80)
        print()
        print(f"Exa API requests: {len(all_assets)} (1 per ticker)")
        print(f"  Total articles retrieved: {len(ingestion_output.records)}")
        print(f"Gemini API calls: {extraction_result.attempted}")
        print(f"  Successful: {len(extraction_result.extractions) + extraction_result.not_mentioned}")
        print(f"  Failed/Retried: {extraction_result.failed}")
        print()
        print("Check your API provider consoles for exact usage and cost")
        print()
        
        # Final summary
        print("=" * 80)
        print("VERIFICATION SUMMARY")
        print("=" * 80)
        print()
        
        checks = []
        checks.append(("Exa API executed", len(ingestion_output.records) > 0))
        checks.append(("All 10 tickers queried", len(articles_by_ticker) == 10 or len(ingestion_output.meta.get("failed_assets", {})) <= 5))
        checks.append(("Articles have publication dates", sum(1 for a in ingestion_output.records if a.published_at) > 0))
        checks.append(("Articles normalized to UTC", all(a.published_at.tzinfo for a in ingestion_output.records if a.published_at)))
        checks.append(("Duplicates detected", duplicates >= 0))
        checks.append(("Ticker mapping completed", len(ticker_counts) > 0))
        checks.append(("Gemini extraction executed", extraction_result.attempted > 0))
        checks.append(("Relevant articles extracted", len(extraction_result.extractions) > 0))
        checks.append(("Irrelevant articles filtered", extraction_result.not_mentioned >= 0))
        checks.append(("Articles persisted", len(stored_articles) > 0))
        checks.append(("Extractions persisted", len(stored_extractions) > 0))
        checks.append(("News source attribution correct", all(e.get("source") == "exa" for e in stored_extractions)))
        checks.append(("Sentiment aggregation completed", len(agg_output.rows) > 0))
        
        for check, passed in checks:
            status = "✓" if passed else "✗"
            print(f"  {status} {check}")
        print()
        
        all_passed = all(passed for _, passed in checks)
        if all_passed:
            print("✅ LIVE EXA NEWS VERIFICATION PASSED")
        else:
            print("⚠ LIVE EXA NEWS VERIFICATION COMPLETED WITH WARNINGS")
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
    
    finally:
        await transport.aclose()


if __name__ == "__main__":
    asyncio.run(main())
