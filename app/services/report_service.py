"""Pure functions that turn pipeline state into rankings and report-ready structures."""
from __future__ import annotations

from collections import Counter, defaultdict

import pandas as pd

from app.services.market_frames import bars_to_frame
from app.utils import to_json_safe, utcnow

DISCLAIMER = ("Predictions are statistical model estimates for research purposes only. They are not financial "
              "advice and carry no guarantee of accuracy.")
TECH_LEVEL_WINDOW = 20


def direction_summary(extractions: list) -> dict:
    counts = Counter(e.direction for e in extractions)
    n_dir = counts["up"] + counts["down"]
    if not extractions:
        return {"direction": None, "net": None, "counts": {}}
    if n_dir == 0:
        return {"direction": "sideways" if counts["sideways"] else "unclear", "net": 0.0, "counts": dict(counts)}
    net = (counts["up"] - counts["down"]) / n_dir
    label = "up" if net > 0.2 else "down" if net < -0.2 else "mixed"
    return {"direction": label, "net": net, "counts": dict(counts)}


def _rank(values: dict, reverse: bool = True) -> dict:
    """Dense 1-based ranks; assets with a missing value are left unranked (None), never ranked as 0."""
    ranked = sorted((k for k, v in values.items() if v is not None and v == v), key=lambda k: values[k], reverse=reverse)
    out = {k: None for k in values}
    for i, k in enumerate(ranked, 1):
        out[k] = i
    return out


def _window_value(window: pd.DataFrame, symbol: str, source_type: str, col: str):
    if window is None or window.empty:
        return None
    r = window[(window.symbol == symbol) & (window.source_type == source_type)]
    if r.empty:
        return None
    v = r.iloc[0][col]
    return None if v != v else float(v)


def rank_assets(assets: list, window: pd.DataFrame, extractions: list) -> list[dict]:
    by_sym = defaultdict(list)
    for e in extractions:
        by_sym[e.symbol].append(e)
    rows = {}
    for a in assets:
        d = direction_summary(by_sym[a.symbol])
        rows[a.symbol] = {
            "symbol": a.symbol, "name": a.name,
            "social_mentions": int(_window_value(window, a.symbol, "social", "mention_count") or 0),
            "sentiment_score": _window_value(window, a.symbol, "social", "weighted_sentiment"),
            "social_sentiment_mean": _window_value(window, a.symbol, "social", "sentiment_mean"),
            "news_articles": int(_window_value(window, a.symbol, "news", "mention_count") or 0),
            "news_sentiment": _window_value(window, a.symbol, "news", "sentiment_mean"),
            "direction": d["direction"], "direction_net": d["net"], "direction_counts": d["counts"]}
    mention_rank = _rank({s: r["social_mentions"] for s, r in rows.items()})
    sent_rank = _rank({s: r["sentiment_score"] for s, r in rows.items()})
    dir_rank = _rank({s: r["direction_net"] for s, r in rows.items()})
    for s, r in rows.items():
        r.update(mention_rank=mention_rank[s], sentiment_rank=sent_rank[s], direction_rank=dir_rank[s])
    return sorted(rows.values(), key=lambda r: (r["mention_rank"] is None, r["mention_rank"], r["symbol"]))


def technical_levels(bars: list) -> dict | None:
    """CALCULATED levels (rolling low/high) - always labelled as such, never mixed with trader-reported levels."""
    df = bars_to_frame(bars).tail(TECH_LEVEL_WINDOW)
    if df.empty:
        return None
    return {"method": f"rolling_{TECH_LEVEL_WINDOW}d_low_high", "calculated": True,
            "support": float(df["low"].min()), "resistance": float(df["high"].max())}


def _clean_records(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    return to_json_safe(df[cols].to_dict("records")) if df is not None and len(df) else []


def build_report(ctx: dict) -> dict:
    """Assemble the dashboard payload. ``ctx`` keys: exchange, run, assets, rankings, agg, extractions,
    articles, bars_by_symbol, feature_rows, predictions, prediction_skipped, evaluation, backtest, history,
    model_info, outcomes, warnings, is_demo."""
    agg, ex = ctx.get("agg"), ctx["exchange"]
    rows = agg.rows if agg is not None else pd.DataFrame()
    window_all = agg.window if agg is not None else pd.DataFrame()
    by_source = agg.by_source if agg is not None else pd.DataFrame()
    daily = agg.daily if agg is not None else pd.DataFrame()
    preds = {p.symbol: p for p in ctx.get("predictions", [])}
    feats = {f.symbol: f for f in ctx.get("feature_rows", [])}
    exts = defaultdict(list)
    for e in ctx.get("extractions", []):
        exts[e.symbol].append(e)
    score_of = {(r["record_id"], r["symbol"]): r["sentiment_score"] for r in rows.to_dict("records")} if len(rows) else {}
    articles = defaultdict(list)
    for a in ctx.get("articles", []):
        for s in a.asset_symbols:
            articles[s].append(a)

    assets_out = []
    for a in ctx["assets"]:
        s = a.symbol
        bars = ctx.get("bars_by_symbol", {}).get(s, [])
        social_w = window_all[(window_all.symbol == s) & (window_all.source_type == "social")] if len(window_all) else window_all
        social = social_w.iloc[0].to_dict() if len(social_w) else {}
        dist = {k: social.get(f"{k}_ratio") for k in ("bullish", "bearish", "neutral")}
        src_rows = by_source[(by_source.symbol == s) & (by_source.source_type == "social")] if len(by_source) else by_source
        news_w = window_all[(window_all.symbol == s) & (window_all.source_type == "news")] if len(window_all) else window_all
        d_rows = daily[(daily.symbol == s) & (daily.scope.isin(["social", "news"]))] if len(daily) else daily
        series = []
        if len(d_rows):
            for bucket, g in d_rows.groupby("bucket_date"):
                rec = {"date": bucket.date().isoformat()}
                for _, r in g.iterrows():
                    rec[f"{r['scope']}_mentions"] = int(r["mention_count"])
                    rec[f"{r['scope']}_sentiment"] = None if r["sentiment_mean"] != r["sentiment_mean"] else float(r["sentiment_mean"])
                series.append(rec)
        ext = sorted(exts[s], key=lambda e: (-(bool(e.position) + bool(e.support_levels or e.resistance_levels) + (e.target_price is not None)), -e.confidence))
        pred = preds.get(s)
        fr = feats.get(s)
        assets_out.append(to_json_safe({
            "symbol": s, "name": a.name,
            "price": {"current": fr.close if fr else (bars[-1].close if bars else None),
                      "as_of_date": fr.as_of_date if fr else (bars[-1].session_date if bars else None),
                      "currency": ex.currency},
            "prediction": None if pred is None else {
                "predicted_price": pred.predicted_price, "predicted_return": pred.predicted_return,
                "target_session_date": pred.target_session_date, "model_version": pred.model_version},
            "prediction_note": None if pred else ctx.get("prediction_skipped", {}).get(s) or ctx.get("prediction_unavailable_reason"),
            "sentiment": {"social": {k: social.get(k) for k in ("mention_count", "scored_count", "sentiment_mean", "weighted_sentiment", "unique_author_count")},
                          "distribution": dist,
                          "news": {k: news_w.iloc[0].get(k) for k in ("mention_count", "sentiment_mean")} if len(news_w) else {}},
            "by_source": _clean_records(src_rows, ["source", "mention_count", "scored_count", "sentiment_mean"]),
            "daily": series,
            "coverage": {"social_posts": int(social.get("mention_count", 0) or 0),
                         "social_scored": int(social.get("scored_count", 0) or 0),
                         "news_articles": int(news_w.iloc[0]["mention_count"]) if len(news_w) else 0,
                         "market_bars": len(bars), "extractions": len(exts[s])},
            "news": [{"title": x.title, "url": x.url, "published_at": x.published_at, "source_domain": x.source_domain,
                      "sentiment_score": score_of.get((x.record_id, s))}
                     for x in sorted(articles[s], key=lambda x: (x.published_at is None, -(x.published_at.timestamp() if x.published_at else 0)))[:8]],
            "intelligence": [{"source": e.source, "source_id": e.record_id, "source_type": e.source_type,
                              "sentiment": e.sentiment, "sentiment_score": e.sentiment_score, "direction": e.direction,
                              "position": e.position, "support_levels": e.support_levels,
                              "resistance_levels": e.resistance_levels, "target_price": e.target_price,
                              "confidence": e.confidence, "evidence_text": e.evidence_text,
                              "timestamp": e.source_timestamp} for e in ext[:15]],
            "technical_levels": technical_levels(bars),
            "history": ctx.get("history", {}).get(s, []),
        }))
    return to_json_safe({
        "is_demo": ctx.get("is_demo", False), "generated_at": utcnow(), "disclaimer": DISCLAIMER,
        "run": ctx["run"], "exchange": {"code": ex.code, "name": ex.name, "timezone": ex.timezone, "currency": ex.currency},
        "warnings": ctx.get("warnings", []), "rankings": ctx.get("rankings", []), "assets": assets_out,
        "model": ctx.get("model_info", {}), "evaluation": ctx.get("evaluation"), "backtest": ctx.get("backtest"),
        "workers": ctx.get("outcomes", [])})
