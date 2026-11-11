import type { Ranking } from "../types";
import { DIRECTION, score, signClass } from "../format";

/** Diverging bar centred on zero: right = bullish, left = bearish. */
function SentimentBar({ value }: { value: number | null }) {
  if (value == null) return <span className="text-flat">no scored posts</span>;
  const w = Math.min(Math.abs(value), 1) * 50;
  return (
    <div className="flex items-center gap-2" role="img" aria-label={`sentiment ${score(value)}`}>
      <div className="relative h-2 w-28 rounded-sm bg-ground">
        <div className="absolute inset-y-0 left-1/2 w-px bg-line" />
        <div className={`absolute inset-y-0 rounded-sm ${value >= 0 ? "bg-up" : "bg-down"}`}
             style={value >= 0 ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }} />
      </div>
      <span className={`w-12 text-right ${signClass(value)}`}>{score(value)}</span>
    </div>
  );
}

export default function Rankings({ rows, selected, onSelect }: { rows: Ranking[]; selected: string; onSelect: (s: string) => void }) {
  const max = Math.max(1, ...rows.map((r) => r.social_mentions));
  return (
    <section aria-labelledby="rank-h" className="rounded-md border border-line bg-surface">
      <header className="border-b border-line px-4 py-3">
        <h2 id="rank-h" className="text-base font-semibold">Asset ranking</h2>
        <p className="text-sm text-muted">Ranked by social mentions in the collection window. Select a row for detail.</p>
      </header>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <thead className="text-left text-muted">
            <tr>
              <th className="px-4 py-2 font-medium">Asset</th>
              <th className="px-2 py-2 font-medium">Social mentions</th>
              <th className="px-2 py-2 font-medium">Sentiment (engagement-weighted)</th>
              <th className="px-2 py-2 font-medium">Direction</th>
              <th className="px-4 py-2 text-right font-medium">News</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const d = r.direction ? DIRECTION[r.direction] : null;
              const active = r.symbol === selected;
              return (
                <tr key={r.symbol} className={`border-t border-line ${active ? "bg-ground" : ""}`}>
                  <td className="px-4 py-2">
                    <button onClick={() => onSelect(r.symbol)} aria-pressed={active} className="text-left">
                      <span className="font-medium">{r.name}</span>
                      <span className="block text-xs text-muted">{r.symbol}</span>
                    </button>
                  </td>
                  <td className="px-2 py-2">
                    <div className="flex items-center gap-2">
                      <span className="w-6 text-right text-muted">{r.mention_rank ?? "–"}</span>
                      <div className="h-2 w-24 rounded-sm bg-ground"><div className="h-2 rounded-sm bg-ink" style={{ width: `${(r.social_mentions / max) * 100}%` }} /></div>
                      <span>{r.social_mentions}</span>
                    </div>
                  </td>
                  <td className="px-2 py-2"><SentimentBar value={r.sentiment_score} /></td>
                  <td className="px-2 py-2">
                    {d ? <span className={d.cls}><span aria-hidden>{d.glyph}</span> {d.label}</span> : <span className="text-flat">no extraction</span>}
                  </td>
                  <td className="px-4 py-2 text-right text-muted">{r.news_articles}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
