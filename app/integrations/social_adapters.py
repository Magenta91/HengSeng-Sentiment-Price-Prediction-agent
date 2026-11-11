"""Config-driven social adapters. Source-specific scraping details live in config
(actor id, input template, field map) - never hardcoded in workers."""
from __future__ import annotations

import re
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from app.core.config_models import SourceConfig
from app.integrations.apify_client import ApifyClient
from app.schemas.records import IngestionRequest, IngestionResult, SocialPost, stable_id
from app.utils import parse_timestamp, utcnow

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def render_template(obj: Any, variables: dict) -> Any:
    """Substitute ``{name}`` placeholders. A string that is exactly one placeholder is replaced
    by the typed value (list/int/...); otherwise placeholders are string-interpolated."""
    if isinstance(obj, str):
        m = _PLACEHOLDER.fullmatch(obj)
        if m and m.group(1) in variables:
            return variables[m.group(1)]
        return _PLACEHOLDER.sub(lambda mm: str(variables.get(mm.group(1), mm.group(0))), obj)
    if isinstance(obj, dict):
        return {k: render_template(v, variables) for k, v in obj.items()}
    if isinstance(obj, list):
        return [render_template(v, variables) for v in obj]
    return obj


def get_path(item: dict, path: str | None) -> Any:
    """Dotted-path lookup, e.g. ``user.followers``."""
    if not path:
        return None
    cur: Any = item
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _to_int(v: Any) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(float(str(v).replace(",", "")))
    except ValueError:
        return None


class SocialSourceAdapter(Protocol):
    source_name: str

    async def fetch(self, request: IngestionRequest) -> IngestionResult: ...


class LIHKGAdapter:
    """Specialized adapter for LIHKG with search-based ingestion.
    
    The actor supports two modes:
    - Search mode: Returns thread metadata + content for threads matching keywords
    - Thread mode: Documented but non-functional (returns 0 items in practice)
    
    Search mode provides own_text (thread content) and content fields,
    giving us full thread text without needing a separate detail fetch.
    
    Note: Individual replies are not accessible through search mode.
    Reply counts are available in metadata but reply text is not retrieved.
    
    Implementation uses enable_full_content flag for future compatibility,
    but currently falls back to search-only mode due to thread mode limitations.
    """

    def __init__(self, source: SourceConfig, client: ApifyClient, *, enable_full_content: bool = True):
        self.source = source
        self.source_name = source.name
        self.client = client
        self.enable_full_content = enable_full_content  # Reserved for future use

    def build_input(self, request: IngestionRequest) -> dict:
        """Build actor input for search mode."""
        queries = sorted({q for a in request.assets for q in a.queries()})
        return render_template(self.source.input_template, {
            "queries": queries, 
            "since_iso": request.window_start.isoformat(),
            "until_iso": request.window_end.isoformat(), 
            "max_items": min(request.max_items, self.source.max_items)
        })

    def _normalize_thread(self, item: dict, provenance: dict) -> SocialPost | None:
        """Normalize a thread record."""
        fm = self.source.field_map.get("thread", {})
        
        # Try own_text first, then content, then title as fallback
        text = (get_path(item, fm.get("own_text")) or 
                get_path(item, fm.get("text")) or 
                get_path(item, fm.get("title")))
        
        if not isinstance(text, str) or not text.strip():
            return None
        
        # If we used title as main text, don't prepend it again
        title = get_path(item, fm.get("title"))
        used_title_as_text = (not get_path(item, fm.get("own_text")) and 
                              not get_path(item, fm.get("text")) and 
                              title)
        
        if title and isinstance(title, str) and not used_title_as_text:
            text = f"{title}\n\n{text}"
        
        tz = ZoneInfo(self.source.source_timezone)
        posted_at = parse_timestamp(get_path(item, fm.get("timestamp")), assume_tz=tz)
        author = get_path(item, fm.get("author_id"))
        thread_id = get_path(item, fm.get("id"))
        post_id = f"thread-{thread_id}" if thread_id else stable_id(text, posted_at, author)
        
        return SocialPost(
            source=self.source.name, source_post_id=post_id, text=text, posted_at=posted_at,
            author_id=None if author is None else str(author),
            author_followers=None,  # Not available in LIHKG
            likes=_to_int(get_path(item, fm.get("likes"))),
            reposts=None,  # Not available in LIHKG
            views=None,  # Not available in LIHKG
            url=get_path(item, fm.get("url")),
            language=self.source.language,
            provenance=dict(provenance, record_type="thread", thread_id=thread_id),
            raw=item)

    async def fetch(self, request: IngestionRequest) -> IngestionResult:
        """Fetch LIHKG threads using search mode with local date filtering.
        
        Thread mode is documented but non-functional, so we use search mode only.
        Search mode provides thread content in own_text/content fields.
        """
        problem = self.source.operational_problem()
        if problem:
            raise RuntimeError(f"source {self.source.name} not operational: {problem}")
        
        # Fetch using search mode
        search_input = self.build_input(request)
        search_res = await self.client.run_and_fetch(
            self.source.actor_id, 
            search_input,
            max_items=min(request.max_items, self.source.max_items)
        )
        
        prov_base = {
            "retrieved_at": utcnow(), 
            "actor_id": search_res.actor_id, 
            "run_id": search_res.run_id,
            "dataset_id": search_res.dataset_id,
            "query": ", ".join(search_input.get("keywords", search_input.get("searchQueries", [])))
            if isinstance(search_input.get("keywords") or search_input.get("searchQueries"), list) else None
        }
        
        # Apply date filter and normalize threads
        posts = []
        invalid = 0
        stale_count = 0
        
        for item in search_res.items:
            # Check if this is a thread (has thread_id)
            if not ('thread_id' in item and item.get('thread_id') is not None):
                continue
            
            # Check date
            tz = ZoneInfo(self.source.source_timezone)
            created_at_raw = get_path(item, self.source.field_map.get("thread", {}).get("timestamp"))
            if created_at_raw:
                posted_at = parse_timestamp(created_at_raw, assume_tz=tz)
                if not (posted_at and request.window_start <= posted_at <= request.window_end):
                    stale_count += 1
                    continue
            else:
                stale_count += 1
                continue
            
            # Normalize thread
            p = self._normalize_thread(item, prov_base)
            if p is None:
                invalid += 1
            else:
                posts.append(p)
        
        return IngestionResult(
            self.source.name, "social", posts,
            {
                "actor_id": search_res.actor_id, 
                "run_id": search_res.run_id,
                "dataset_id": search_res.dataset_id,
                "items_fetched": len(search_res.items),
                "stale_items": stale_count,
                "invalid_items": invalid,
                "threads": len(posts),
                "replies": 0,  # Not accessible via search mode
                "note": "Thread mode non-functional; using search mode only"
            }
        )


class ApifySocialAdapter:
    def __init__(self, source: SourceConfig, client: ApifyClient):
        self.source = source
        self.source_name = source.name
        self.client = client

    def build_input(self, request: IngestionRequest) -> dict:
        queries = sorted({q for a in request.assets for q in a.queries()})
        return render_template(self.source.input_template, {
            "queries": queries, "since_iso": request.window_start.isoformat(),
            "until_iso": request.window_end.isoformat(), "max_items": min(request.max_items, self.source.max_items)})

    def normalize(self, item: dict, provenance: dict) -> SocialPost | None:
        fm = self.source.field_map
        text = get_path(item, fm.get("text"))
        if not isinstance(text, str) or not text.strip():
            return None
        tz = ZoneInfo(self.source.source_timezone)
        posted_at = parse_timestamp(get_path(item, fm.get("timestamp")), assume_tz=tz)
        author = get_path(item, fm.get("author_id"))
        raw_id = get_path(item, fm.get("id"))
        post_id = str(raw_id) if raw_id not in (None, "") else stable_id(text, posted_at, author)
        return SocialPost(
            source=self.source.name, source_post_id=post_id, text=text, posted_at=posted_at,
            author_id=None if author is None else str(author),
            author_followers=_to_int(get_path(item, fm.get("followers"))),
            likes=_to_int(get_path(item, fm.get("likes"))), reposts=_to_int(get_path(item, fm.get("reposts"))),
            views=_to_int(get_path(item, fm.get("views"))), url=get_path(item, fm.get("url")),
            language=self.source.language, provenance=dict(provenance), raw=item)

    async def fetch(self, request: IngestionRequest) -> IngestionResult:
        problem = self.source.operational_problem()
        if problem:
            raise RuntimeError(f"source {self.source.name} not operational: {problem}")
        run_input = self.build_input(request)
        res = await self.client.run_and_fetch(self.source.actor_id, run_input,
                                              max_items=min(request.max_items, self.source.max_items))
        prov = {"retrieved_at": utcnow(), "actor_id": res.actor_id, "apify_run_id": res.run_id,
                "dataset_id": res.dataset_id, "query": ", ".join(run_input.get("queries", []))
                if isinstance(run_input.get("queries"), list) else None}
        posts, invalid = [], 0
        for item in res.items:
            p = self.normalize(item, prov)
            if p is None:
                invalid += 1
            else:
                posts.append(p)
        return IngestionResult(self.source.name, "social", posts,
                               {"actor_id": res.actor_id, "apify_run_id": res.run_id, "dataset_id": res.dataset_id,
                                "items_fetched": len(res.items), "invalid_items": invalid})


class XueqiuAdapter:
    """Specialized adapter for Xueqiu that executes separately per ticker.
    
    The Xueqiu Apify actor accepts ONLY ONE stock code per execution.
    The application must execute the actor separately for each ticker
    and aggregate the results.
    
    Stock code mapping for HKEX:
    - 0700.HK → 0700
    - 9988.HK → 9988
    - 0005.HK → 0005
    - 1299.HK → 1299
    - 3690.HK → 3690
    """

    def __init__(self, source: SourceConfig, client: ApifyClient):
        self.source = source
        self.source_name = source.name
        self.client = client

    def _extract_stock_code(self, ticker_symbol: str) -> str:
        """Extract Xueqiu stock code from ticker symbol.
        
        For HKEX: 0700.HK → 0700
        Removes the exchange suffix.
        """
        return ticker_symbol.split('.')[0]

    def build_input(self, stock_code: str, max_items: int) -> dict:
        """Build input for a single ticker execution."""
        return render_template(self.source.input_template, {
            "stock_code": stock_code,
            "max_items": max_items
        })

    def normalize(self, item: dict, provenance: dict) -> SocialPost | None:
        """Normalize a Xueqiu post record."""
        fm = self.source.field_map
        
        # Get title/text
        text = get_path(item, fm.get("text"))
        if not isinstance(text, str) or not text.strip():
            return None
        
        tz = ZoneInfo(self.source.source_timezone)
        posted_at = parse_timestamp(get_path(item, fm.get("timestamp")), assume_tz=tz)
        author = get_path(item, fm.get("author_id"))
        
        # Try to get stable ID from URL, fallback to generated ID
        url = get_path(item, fm.get("url"))
        raw_id = get_path(item, fm.get("id"))
        if raw_id and isinstance(raw_id, str):
            # Use URL-based ID for stability
            post_id = stable_id(raw_id)
        elif url:
            post_id = stable_id(url)
        else:
            post_id = stable_id(text, posted_at, author)
        
        # Get engagement metrics
        # Xueqiu provides: likes, comments (not reposts), followers
        # Views and reposts are not available
        likes = _to_int(get_path(item, fm.get("likes")))
        comments = _to_int(get_path(item, fm.get("comments")))
        
        return SocialPost(
            source=self.source.name,
            source_post_id=post_id,
            text=text,
            posted_at=posted_at,
            author_id=None if author is None else str(author),
            author_followers=_to_int(get_path(item, fm.get("followers"))),
            likes=likes,
            reposts=None,  # Not available in Xueqiu
            views=None,  # Not available in Xueqiu
            url=url,
            language=self.source.language,
            provenance=dict(provenance, comments=comments),  # Store comments in provenance
            raw=item
        )

    async def fetch(self, request: IngestionRequest) -> IngestionResult:
        """Execute actor separately for each ticker and aggregate results."""
        problem = self.source.operational_problem()
        if problem:
            raise RuntimeError(f"source {self.source.name} not operational: {problem}")
        
        all_posts = []
        invalid = 0
        stale_count = 0
        execution_meta = []
        failed_tickers = {}
        
        # Execute separately for each ticker
        for asset in request.assets:
            stock_code = self._extract_stock_code(asset.symbol)
            
            try:
                # Build input for this specific ticker
                run_input = self.build_input(stock_code, min(request.max_items, self.source.max_items))
                
                # Execute actor for this ticker
                res = await self.client.run_and_fetch(
                    self.source.actor_id,
                    run_input,
                    max_items=self.source.max_items
                )
                
                prov = {
                    "retrieved_at": utcnow(),
                    "actor_id": res.actor_id,
                    "apify_run_id": res.run_id,
                    "dataset_id": res.dataset_id,
                    "stock_code": stock_code,
                    "ticker_symbol": asset.symbol,
                    "ticker_name": asset.name
                }
                
                # Track execution metadata
                execution_meta.append({
                    "ticker": asset.symbol,
                    "stock_code": stock_code,
                    "run_id": res.run_id,
                    "items_fetched": len(res.items)
                })
                
                # Normalize and filter each post
                for item in res.items:
                    # Normalize
                    p = self.normalize(item, prov)
                    if p is None:
                        invalid += 1
                        continue
                    
                    # Apply date filter
                    if p.posted_at:
                        if not (request.window_start <= p.posted_at <= request.window_end):
                            stale_count += 1
                            continue
                    else:
                        # No timestamp - skip
                        stale_count += 1
                        continue
                    
                    all_posts.append(p)
                    
            except Exception as e:
                # Record failure but continue with remaining tickers
                failed_tickers[asset.symbol] = f"{type(e).__name__}: {str(e)}"
        
        # If all tickers failed, raise the last exception
        if len(failed_tickers) == len(request.assets):
            raise RuntimeError(f"All Xueqiu executions failed: {failed_tickers}")
        
        return IngestionResult(
            self.source.name, "social", all_posts,
            {
                "actor_id": self.source.actor_id,
                "executions": len(execution_meta),
                "execution_details": execution_meta,
                "total_items_fetched": sum(e["items_fetched"] for e in execution_meta),
                "stale_items": stale_count,
                "invalid_items": invalid,
                "posts": len(all_posts),
                "failed_tickers": failed_tickers,
                "note": "One actor execution per ticker"
            }
        )


class NaverStockDiscussionAdapter:
    """Adapter for Naver Stock Discussion scraper.
    
    The actor accepts multiple stock codes and returns discussions from
    Naver's stock discussion boards. Supports holder verification metadata.
    
    Stock code mapping for KRX:
    - 005930.KS → 005930
    - 000660.KS → 000660
    - 005380.KS → 005380
    - 373220.KS → 373220
    - 068270.KS → 068270
    """

    def __init__(self, source: SourceConfig, client: ApifyClient):
        self.source = source
        self.source_name = source.name
        self.client = client

    def _extract_stock_code(self, ticker_symbol: str) -> str:
        """Extract stock code from ticker symbol.
        
        For KRX: 005930.KS → 005930
        Removes the exchange suffix.
        """
        return ticker_symbol.split('.')[0]

    def build_input(self, request: IngestionRequest) -> dict:
        """Build input with all stock codes."""
        stock_codes = [self._extract_stock_code(a.symbol) for a in request.assets]
        return render_template(self.source.input_template, {
            "stock_codes": stock_codes,
            "max_items": min(request.max_items, self.source.max_items)
        })

    def normalize(self, item: dict, provenance: dict) -> SocialPost | None:
        """Normalize a Naver stock discussion post."""
        fm = self.source.field_map
        
        # Combine title and text
        title = get_path(item, fm.get("title"))
        content = get_path(item, fm.get("content"))
        
        # Build full text
        if title and content:
            text = f"{title}\n\n{content}"
        elif title:
            text = title
        elif content:
            text = content
        else:
            return None
        
        if not isinstance(text, str) or not text.strip():
            return None
        
        tz = ZoneInfo(self.source.source_timezone)
        posted_at = parse_timestamp(get_path(item, fm.get("timestamp")), assume_tz=tz)
        author = get_path(item, fm.get("author_id"))
        
        # Get stable ID from URL or generate one
        url = get_path(item, fm.get("url"))
        if url:
            post_id = stable_id(url)
        else:
            post_id = stable_id(text, posted_at, author)
        
        # Get engagement metrics
        likes = _to_int(get_path(item, fm.get("likes")))
        dislikes = _to_int(get_path(item, fm.get("dislikes")))
        views = _to_int(get_path(item, fm.get("views")))
        
        # Get Naver-specific metadata
        stock_name = get_path(item, fm.get("stock_name"))
        is_holder_verified = get_path(item, fm.get("is_holder_verified"))
        
        return SocialPost(
            source=self.source.name,
            source_post_id=post_id,
            text=text,
            posted_at=posted_at,
            author_id=None if author is None else str(author),
            author_followers=None,  # Not available in Naver
            likes=likes,
            reposts=None,  # Not available
            views=views,
            url=url,
            language=self.source.language,
            provenance=dict(
                provenance,
                stock_name=stock_name,
                is_holder_verified=is_holder_verified,
                dislikes=dislikes
            ),
            raw=item
        )

    async def fetch(self, request: IngestionRequest) -> IngestionResult:
        """Fetch Naver stock discussions for all tickers in one execution."""
        problem = self.source.operational_problem()
        if problem:
            raise RuntimeError(f"source {self.source.name} not operational: {problem}")
        
        # Build input with all stock codes
        run_input = self.build_input(request)
        
        # Execute actor once for all tickers
        res = await self.client.run_and_fetch(
            self.source.actor_id,
            run_input,
            max_items=min(request.max_items * len(request.assets), 1000)  # Budget control
        )
        
        prov_base = {
            "retrieved_at": utcnow(),
            "actor_id": res.actor_id,
            "apify_run_id": res.run_id,
            "dataset_id": res.dataset_id,
            "requested_stocks": [self._extract_stock_code(a.symbol) for a in request.assets]
        }
        
        posts = []
        invalid = 0
        stale_count = 0
        
        # Normalize and filter each post
        for item in res.items:
            # Normalize
            p = self.normalize(item, prov_base)
            if p is None:
                invalid += 1
                continue
            
            # Apply date filter
            if p.posted_at:
                if not (request.window_start <= p.posted_at <= request.window_end):
                    stale_count += 1
                    continue
            else:
                # No timestamp - skip
                stale_count += 1
                continue
            
            posts.append(p)
        
        return IngestionResult(
            self.source.name, "social", posts,
            {
                "actor_id": res.actor_id,
                "apify_run_id": res.run_id,
                "dataset_id": res.dataset_id,
                "items_fetched": len(res.items),
                "stale_items": stale_count,
                "invalid_items": invalid,
                "posts": len(posts),
                "note": "Single execution for all KRX tickers"
            }
        )
