"""SYNTHETIC demo data sources. Everything here is fabricated for local demonstration and tests
and is labelled ``demo`` in provenance, run mode and the dashboard. It is NOT real market data."""
from __future__ import annotations

import hashlib
import json
import random
import re
from datetime import date, datetime, timedelta

from app.schemas.records import IngestionRequest, IngestionResult, MarketBar, SocialPost
from app.utils import UTC, utcnow


def _rng(*parts) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16))


def base_price(symbol: str) -> float:
    return round(40 + _rng("base", symbol).random() * 460, 2)


BULL = ["{a} breakout looking strong, support {s}, resistance {r}. Long from {e}, target {t}.",
        "Loading more {a} here, bullish. Support at {s}.",
        "{a} earnings beat, I am bullish, expecting a push toward {t}."]
BEAR = ["{a} looks weak, bearish. Resistance {r} rejected again.",
        "Short {a} from {e}, breakdown below support {s}.",
        "Selling {a}, regulation risk and falling volume."]
NEUTRAL = ["{a} just ranging today, no view.", "Anyone following {a}? Volume is thin."]


class DemoSocialAdapter:
    def __init__(self, source_name: str, posts_per_asset: int = 12, with_engagement: bool = True):
        self.source_name, self.n, self.with_engagement = source_name, posts_per_asset, with_engagement

    async def fetch(self, request: IngestionRequest) -> IngestionResult:
        posts, span = [], (request.window_end - request.window_start).total_seconds() - 7200
        retrieved = utcnow()
        for asset in request.assets:
            px, alias = base_price(asset.symbol), (asset.aliases[-1] if asset.aliases else asset.name)
            for i in range(self.n):
                r = _rng(self.source_name, asset.symbol, i)
                tpl = r.choice(BULL * 2 + BEAR * 2 + NEUTRAL)
                text = tpl.format(a=alias, s=round(px * 0.95, 1), r=round(px * 1.05, 1), e=round(px, 1), t=round(px * 1.1, 1))
                posted = request.window_start + timedelta(seconds=r.random() * span)
                likes = r.randint(0, 800) if self.with_engagement else None
                posts.append(SocialPost(
                    source=self.source_name, source_post_id=f"demo-{asset.symbol}-{i}", text=text, posted_at=posted,
                    author_id=f"user{r.randint(1, 8)}", author_followers=r.randint(10, 20000),
                    likes=likes, reposts=r.randint(0, 80) if self.with_engagement else None,
                    views=r.randint(100, 50000), language="demo",
                    provenance={"retrieved_at": retrieved, "demo": True, "query": alias}, raw={"demo": True}))
            # an exact duplicate (re-post) to exercise de-duplication
            dup = posts[-1]
            posts.append(SocialPost(**{**dup.__dict__, "source_post_id": dup.source_post_id + "-dup"}))
        return IngestionResult(self.source_name, "social", posts, {"demo": True})


class DemoNewsClient:
    """Same interface as ExaClient.search_news."""

    async def search_news(self, query: str, *, start: datetime, end: datetime, num_results: int = 10) -> list[dict]:
        name = re.sub(r" stock news$", "", query)
        out = []
        for i in range(min(num_results, 4)):
            r = _rng("news", name, i)
            pub = start + timedelta(seconds=r.random() * (end - start).total_seconds() * 0.95)
            tone = r.choice(["rallies on strong results", "faces regulatory pressure", "holds steady ahead of earnings"])
            out.append({"id": f"demo-news-{name}-{i}", "url": f"https://news.example.com/{name.replace(' ', '-')}/{i}",
                        "title": f"{name} {tone}", "text": f"{name} {tone}. Analysts remain divided on the outlook.",
                        "publishedDate": pub.isoformat(), "score": round(0.5 + r.random() * 0.5, 3)})
        return out


class DemoMarketProvider:
    name = "demo"

    async def get_daily_bars(self, symbol: str, start: date, end: date, calendar) -> list[MarketBar]:
        # Always walk from a fixed anchor so every call yields the same price path regardless of `start`.
        rng, price, bars, d = _rng("bars", symbol), base_price(symbol), [], date(2022, 1, 1)
        while d <= end:
            if calendar.is_trading_day(d):
                o = price
                price = max(1.0, price * (1 + rng.gauss(0.0003, 0.015)))
                hi, lo = max(o, price) * (1 + abs(rng.gauss(0, 0.004))), min(o, price) * (1 - abs(rng.gauss(0, 0.004)))
                if d >= start:
                    bars.append(MarketBar(symbol, d, calendar.session_close_utc(d).astimezone(UTC), round(o, 3), round(hi, 3),
                                          round(lo, 3), round(price, 3), float(rng.randint(10**6, 10**7)),
                                          adj_close=round(price, 3), provider="demo"))
                else:
                    rng.randint(10**6, 10**7)
            d += timedelta(days=1)
        return bars


class DemoLLMClient:
    """Rule-based stand-in that mimics an LLM's JSON output so the real parse/validate/guard path runs."""

    model = "demo-rule-based"

    async def complete(self, system: str, user: str, *, max_tokens: int = 1000) -> str:
        m = re.search(r"ASSET:\s*(\S+)\s*\((.*?)\)", user)
        symbol, name = (m.group(1), m.group(2)) if m else ("UNKNOWN", None)
        text = user.split("TEXT:\n", 1)[-1]
        low = text.lower()
        score = 0.0
        score += 0.6 * any(w in low for w in ("bullish", "breakout", "loading", "beat", "long from"))
        score -= 0.6 * any(w in low for w in ("bearish", "weak", "short ", "selling", "breakdown", "falling"))
        sentiment = "bullish" if score > 0.15 else "bearish" if score < -0.15 else "neutral"

        def levels(word):
            return [float(x) for x in re.findall(rf"{word}\D{{0,10}}?(\d+(?:\.\d+)?)", text, flags=re.IGNORECASE)]

        pos_m = re.search(r"(Long|Short) from (\d+(?:\.\d+)?)", text, flags=re.IGNORECASE)
        tgt = levels("target")
        obj = {"asset_symbol": symbol, "asset_name": name, "mentioned": True, "sentiment": sentiment,
               "sentiment_score": score, "direction": {"bullish": "up", "bearish": "down"}.get(sentiment, "sideways"),
               "position": {"type": pos_m.group(1).lower(), "quantity": None, "entry_price": float(pos_m.group(2)),
                            "evidence_text": pos_m.group(0)} if pos_m else None,
               "support_levels": levels("support"), "resistance_levels": levels("resistance"),
               "target_price": tgt[0] if tgt else None, "confidence": 0.7, "evidence_text": text[:80].strip()}
        return json.dumps(obj)
