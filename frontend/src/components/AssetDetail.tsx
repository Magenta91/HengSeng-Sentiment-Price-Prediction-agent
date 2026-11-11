import type { ReactNode } from "react";
import { Bar, CartesianGrid, ComposedChart, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { AssetReport } from "../types";
import { DIRECTION, dateShort, num, pct, score, signClass } from "../format";

const Card = ({ title, children, note }: { title: string; children: ReactNode; note?: string }) => (
  <section className="rounded-md border border-line bg-surface p-4">
    <h3 className="text-sm font-semibold">{title}</h3>
    {note && <p className="mb-2 text-xs text-muted">{note}</p>}
    <div className={note ? "" : "mt-2"}>{children}</div>
  </section>
);

function Distribution({ d }: { d: AssetReport["sentiment"]["distribution"] }) {
  const parts = [["Bullish", d.bullish, "bg-up"], ["Neutral", d.neutral, "bg-flat"], ["Bearish", d.bearish, "bg-down"]] as const;
  if (parts.every(([, v]) => v == null)) return <p className="text-sm text-muted">No scored posts in this window, so there is no distribution. Missing sentiment is not shown as neutral.</p>;
  return (
    <>
      <div className="flex h-3 overflow-hidden rounded-sm bg-ground" role="img"
           aria-label={parts.map(([l, v]) => `${l} ${pct(v, 0)}`).join(", ")}>
        {parts.map(([l, v, c]) => <div key={l} className={c} style={{ width: `${(v ?? 0) * 100}%` }} />)}
      </div>
      <dl className="mt-2 flex gap-4 text-sm">
        {parts.map(([l, v, c]) => <div key={l} className="flex items-center gap-1.5"><span className={`h-2 w-2 rounded-sm ${c}`} /><dt className="text-muted">{l}</dt><dd>{pct(v, 0).replace("+", "")}</dd></div>)}
      </dl>
    </>
  );
}

export default function AssetDetail({ a, currency }: { a: AssetReport; currency: string }) {
  const p = a.prediction;
  const history = a.history.map((h) => ({ date: dateShort(h.target_session_date), predicted: h.predicted_price, actual: h.actual_price }));
  const scoredHistory = history.filter((h) => h.actual != null).length;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Price and next-session estimate" note="Model estimate for the next trading session. Not financial advice.">
        <div className="grid grid-cols-3 gap-3">
          <div><p className="text-xs text-muted">Last close ({dateShort(a.price.as_of_date)})</p><p className="text-xl font-semibold">{num(a.price.current)}</p><p className="text-xs text-muted">{currency}</p></div>
          <div><p className="text-xs text-muted">Estimated close ({dateShort(p?.target_session_date)})</p><p className="text-xl font-semibold">{p ? num(p.predicted_price) : "n/a"}</p></div>
          <div><p className="text-xs text-muted">Estimated return</p><p className={`text-xl font-semibold ${signClass(p?.predicted_return)}`}>{p ? pct(p.predicted_return, 3) : "n/a"}</p></div>
        </div>
        {!p && <p className="mt-3 rounded bg-ground p-2 text-sm text-muted">No prediction: {a.prediction_note ?? "reason not recorded"}</p>}
        {p && <p className="mt-3 text-xs text-muted">Model version {p.model_version}</p>}
      </Card>

      <Card title="Sentiment distribution" note={`${a.coverage.social_scored} of ${a.coverage.social_posts} posts scored · mean ${score(a.sentiment.social.weighted_sentiment)} (engagement-weighted)`}>
        <Distribution d={a.sentiment.distribution} />
        <h4 className="mt-4 text-xs font-medium text-muted">By source</h4>
        {a.by_source.length === 0 ? <p className="text-sm text-muted">No social source returned posts for this asset.</p> :
          <table className="mt-1 w-full text-sm"><tbody>{a.by_source.map((s) => (
            <tr key={s.source} className="border-t border-line"><td className="py-1">{s.source}</td><td className="text-right">{s.mention_count} posts</td>
              <td className={`text-right ${signClass(s.sentiment_mean)}`}>{score(s.sentiment_mean)}</td></tr>))}</tbody></table>}
      </Card>

      <Card title="Daily mentions and sentiment" note="Sessions follow the exchange calendar; posts after the close count toward the next session.">
        {a.daily.length === 0 ? <p className="text-sm text-muted">No timestamped records in the window.</p> :
          <div className="h-56" role="img" aria-label="Daily social mentions and sentiment">
            <ResponsiveContainer><ComposedChart data={a.daily}>
              <CartesianGrid stroke="#D3DAE2" vertical={false} />
              <XAxis dataKey="date" tickFormatter={(d) => d.slice(5)} fontSize={11} />
              <YAxis yAxisId="l" fontSize={11} width={30} /><YAxis yAxisId="r" orientation="right" domain={[-1, 1]} fontSize={11} width={34} />
              <Tooltip /><Legend />
              <Bar yAxisId="l" dataKey="social_mentions" name="Social mentions" fill="#0E1A2B" barSize={14} />
              <Line yAxisId="r" dataKey="social_sentiment" name="Social sentiment" stroke="#1F4FD8" strokeWidth={2} connectNulls={false} dot />
              <Line yAxisId="r" dataKey="news_sentiment" name="News sentiment" stroke="#BF6408" strokeWidth={2} strokeDasharray="4 3" connectNulls={false} dot />
            </ComposedChart></ResponsiveContainer></div>}
      </Card>

      <Card title="Predicted vs actual close" note="Each prediction is scored once its next session has closed.">
        {history.length === 0 ? <p className="text-sm text-muted">No stored predictions yet.</p> :
          <>
            <div className="h-56" role="img" aria-label="Predicted versus actual close">
              <ResponsiveContainer><LineChart data={history}>
                <CartesianGrid stroke="#D3DAE2" vertical={false} /><XAxis dataKey="date" tickFormatter={(d) => d.slice(5)} fontSize={11} />
                <YAxis domain={["auto", "auto"]} fontSize={11} width={48} /><Tooltip /><Legend />
                <Line dataKey="predicted" name="Predicted" stroke="#1F4FD8" strokeWidth={2} />
                <Line dataKey="actual" name="Actual" stroke="#0E1A2B" strokeWidth={2} connectNulls={false} />
              </LineChart></ResponsiveContainer></div>
            <p className="mt-1 text-xs text-muted">{scoredHistory} of {history.length} predictions scored so far.</p>
          </>}
      </Card>

      <Card title="Extracted intelligence" note="Positions and levels appear only when the source text states them. Evidence is quoted from the source.">
        {a.intelligence.length === 0 ? <p className="text-sm text-muted">No extractions. Configure an LLM key to enable structured extraction.</p> :
          <ul className="max-h-96 space-y-3 overflow-y-auto pr-1">{a.intelligence.map((i) => {
            const d = DIRECTION[i.direction] ?? DIRECTION.unclear;
            return (
              <li key={i.source_id} className="border-t border-line pt-2 text-sm first:border-t-0 first:pt-0">
                <p><span className={d.cls}><span aria-hidden>{d.glyph}</span> {d.label}</span> · {i.sentiment} {score(i.sentiment_score)} · {i.source} · confidence {i.confidence.toFixed(2)}</p>
                <p className="text-muted">
                  {i.position?.type ? `Position: ${i.position.type}${i.position.entry_price != null ? ` from ${num(i.position.entry_price)}` : ""}. ` : "No position stated. "}
                  {i.support_levels.length ? `Support ${i.support_levels.join(", ")}. ` : ""}
                  {i.resistance_levels.length ? `Resistance ${i.resistance_levels.join(", ")}. ` : ""}
                  {i.target_price != null ? `Target ${num(i.target_price)}.` : ""}
                </p>
                {i.evidence_text && <blockquote className="mt-1 border-l-2 border-line pl-2 text-muted">“{i.evidence_text}”</blockquote>}
              </li>);
          })}</ul>}
        {a.technical_levels && <p className="mt-3 rounded bg-ground p-2 text-xs text-muted">
          Calculated levels (not from traders): support {num(a.technical_levels.support)}, resistance {num(a.technical_levels.resistance)} — {a.technical_levels.method.replace(/_/g, " ")}.</p>}
      </Card>

      <Card title="Recent news" note={`${a.coverage.news_articles} articles in window`}>
        {a.news.length === 0 ? <p className="text-sm text-muted">No news retrieved for this asset.</p> :
          <ul className="space-y-2 text-sm">{a.news.map((n) => (
            <li key={n.url} className="border-t border-line pt-2 first:border-t-0 first:pt-0">
              <a href={n.url} target="_blank" rel="noreferrer" className="font-medium underline decoration-line underline-offset-2">{n.title}</a>
              <p className="text-xs text-muted">{n.source_domain ?? "unknown source"} · {n.published_at ? `published ${dateShort(n.published_at)}` : "publication date unknown"}{n.sentiment_score != null ? ` · sentiment ${score(n.sentiment_score)}` : ""}</p>
            </li>))}</ul>}
      </Card>
    </div>
  );
}
