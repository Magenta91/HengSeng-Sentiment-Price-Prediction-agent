import type { Exchange, Report, RunStatus } from "../types";
import { num, pct } from "../format";

const chip = (s: string | null) =>
  s === "succeeded" ? "bg-up/10 text-up" : s === "failed" ? "bg-down/10 text-down" : s === "skipped" ? "bg-ground text-muted" : "bg-ground text-ink";

export function RunStatusPanel({ run, report, exchange }: { run: RunStatus | null; report: Report | null; exchange: Exchange | undefined }) {
  const workers = run ? run.workers.map((w) => ({ name: w.worker_name, status: w.status, detail: w.error })) :
    (report?.workers ?? []).map((w) => ({ name: w.name, status: w.status, detail: w.error ?? w.reason }));
  const inactive = exchange?.sources.filter((s) => !s.operational) ?? [];
  return (
    <section aria-labelledby="run-h" className="rounded-md border border-line bg-surface p-4">
      <h2 id="run-h" className="text-base font-semibold">Pipeline run {run ? `· ${run.status}` : report ? `· ${report.run.run_id}` : ""}</h2>
      {inactive.length > 0 && (
        <p className="mt-2 rounded bg-ground p-2 text-sm text-muted">
          Social sources not ready: {inactive.map((s) => `${s.name} (${s.problem})`).join("; ")}. A live run records them as skipped.
        </p>)}
      {workers.length === 0 ? <p className="mt-2 text-sm text-muted">No run yet.</p> :
        <ul className="mt-3 grid gap-1 text-sm sm:grid-cols-2">{workers.map((w) => (
          <li key={w.name} className="flex items-start justify-between gap-2 border-t border-line py-1">
            <span>{w.name}</span>
            <span className="text-right"><span className={`rounded px-1.5 py-0.5 text-xs ${chip(w.status)}`}>{w.status ?? "pending"}</span>
              {w.detail && <span className="block max-w-xs text-xs text-muted">{w.detail}</span>}</span>
          </li>))}</ul>}
    </section>
  );
}

export function ModelPanel({ report }: { report: Report }) {
  const m = report.model;
  if (!m.available) return (
    <section className="rounded-md border border-line bg-surface p-4"><h2 className="text-base font-semibold">Model</h2>
      <p className="mt-2 text-sm text-muted">No model loaded: {m.reason}</p></section>);
  const t = m.metrics?.test;
  const live = report.evaluation && report.evaluation.n > 0 ? report.evaluation : null;
  return (
    <section aria-labelledby="model-h" className="rounded-md border border-line bg-surface p-4">
      <h2 id="model-h" className="text-base font-semibold">Model and validation</h2>
      <p className="mt-1 text-sm">{m.model_name} · v{m.model_version} · {m.feature_set === "market_only" ? "market features only" : "market + sentiment features"}</p>
      {m.demo && <p className="mt-1 text-sm text-demo">Demo model trained on synthetic prices. Metrics below are meaningless.</p>}
      <table className="mt-3 w-full text-sm">
        <thead className="text-left text-muted"><tr><th className="font-medium">Held-out test period</th><th className="font-medium">MAE</th><th className="font-medium">RMSE</th><th className="font-medium">Direction</th></tr></thead>
        <tbody>
          {t ? <>
            <tr className="border-t border-line"><td>Model</td><td>{pct(t.model.mae, 3).replace("+", "")}</td><td>{pct(t.model.rmse, 3).replace("+", "")}</td><td>{t.model.directional_accuracy == null ? "n/a" : pct(t.model.directional_accuracy, 1).replace("+", "")}</td></tr>
            <tr className="border-t border-line"><td>Zero-return baseline</td><td>{pct(t.baseline_zero_return.mae, 3).replace("+", "")}</td><td>{pct(t.baseline_zero_return.rmse, 3).replace("+", "")}</td><td>n/a</td></tr></> :
            <tr><td colSpan={4} className="pt-2 text-muted">No evaluation metrics in the artifact.</td></tr>}
        </tbody>
      </table>
      {t && <p className="mt-2 text-xs text-muted">MAE vs baseline: {t.mae_improvement_pct == null ? "n/a" : `${t.mae_improvement_pct.toFixed(2)}%`} (positive = better than predicting no change). Past test results do not guarantee future accuracy.</p>}
      <p className="mt-2 text-xs text-muted">
        Live scoring: {live ? `${live.n} predictions scored, MAE ${pct(live.model?.mae, 3).replace("+", "")} vs baseline ${pct(live.baseline_zero_return?.mae, 3).replace("+", "")}` : "none yet. Scores appear after predicted sessions close."}
        {" "}Local backtest: {report.backtest?.status === "ok" && report.backtest.overall ? `MAE ${num(report.backtest.overall.model.mae! * 100, 3)}% over ${report.backtest.overall.model.n} rows` : report.backtest?.reason ?? "not run"}.
      </p>
    </section>
  );
}
