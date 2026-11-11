import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import AssetDetail from "./components/AssetDetail";
import Rankings from "./components/Rankings";
import { ModelPanel, RunStatusPanel } from "./components/StatusPanels";
import type { Exchange, Report, RunStatus } from "./types";

type Mode = "live" | "demo";
const FINISHED = ["succeeded", "partial", "failed"];

export default function App() {
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [code, setCode] = useState("");
  const [mode, setMode] = useState<Mode>("live");
  const [report, setReport] = useState<Report | null>(null);
  const [run, setRun] = useState<RunStatus | null>(null);
  const [selected, setSelected] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const loadReport = useCallback(async (ex: string, m: Mode) => {
    try {
      const r = await api.report(ex, m);
      setReport(r);
      setSelected((cur) => (r && r.assets.some((a) => a.symbol === cur) ? cur : r?.rankings[0]?.symbol ?? r?.assets[0]?.symbol ?? ""));
    } catch (e) { setError((e as Error).message); }
  }, []);

  useEffect(() => {
    api.exchanges().then((ex) => { setExchanges(ex); setCode(ex[0]?.code ?? ""); }).catch((e) => setError(`Cannot reach the API: ${e.message}`));
  }, []);
  useEffect(() => { if (code) { setRun(null); void loadReport(code, mode); } }, [code, mode, loadReport]);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const poll = useCallback((runId: string) => {
    timer.current = window.setTimeout(async () => {
      try {
        const st = await api.run(runId);
        setRun(st);
        if (st.status && FINISHED.includes(st.status)) { setLoading(false); await loadReport(code, mode); } else poll(runId);
      } catch (e) { setError((e as Error).message); setLoading(false); }
    }, 1500);
  }, [code, mode, loadReport]);

  const start = async () => {
    setError(null); setLoading(true);
    try { const { run_id } = await api.startRun(code, mode); setRun({ run_id, status: "pending", error: null, warnings: [], workers: [] }); poll(run_id); }
    catch (e) { setError((e as Error).message); setLoading(false); }
  };

  const exchange = exchanges.find((e) => e.code === code);
  const asset = report?.assets.find((a) => a.symbol === selected);
  const warnings = run?.warnings?.length ? run.warnings : report?.warnings ?? [];

  return (
    <div className="mx-auto max-w-7xl px-4 pb-16">
      <header className="flex flex-wrap items-end justify-between gap-4 py-6">
        <div>
          <h1 className="text-2xl font-semibold">Asia equities sentiment desk</h1>
          <p className="text-sm text-muted">Social and news sentiment, extracted trader levels, and a next-session return estimate.</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <label className="text-sm">Exchange{" "}
            <select value={code} onChange={(e) => setCode(e.target.value)} className="ml-1 rounded border border-line bg-surface px-2 py-1.5">
              {exchanges.map((e) => <option key={e.code} value={e.code}>{e.name}</option>)}
            </select></label>
          <label className="text-sm">Data{" "}
            <select value={mode} onChange={(e) => setMode(e.target.value as Mode)} className="ml-1 rounded border border-line bg-surface px-2 py-1.5">
              <option value="live">Live sources</option><option value="demo">Demo (synthetic)</option>
            </select></label>
          <button onClick={start} disabled={loading || !code} className="rounded bg-ink px-4 py-1.5 text-sm font-medium text-surface disabled:opacity-50">
            {loading ? "Running pipeline…" : "Run pipeline"}</button>
        </div>
      </header>

      {(mode === "demo" || report?.is_demo) && (
        <p role="status" className="mb-4 rounded border border-demo/30 bg-demoSoft px-3 py-2 text-sm text-demo">
          Demo mode: posts, news, prices and the model are synthetic and exist only to show how the pipeline works. None of it is real market data.</p>)}
      {error && <p role="alert" className="mb-4 rounded border border-down/40 bg-down/10 px-3 py-2 text-sm">{error}</p>}
      {warnings.length > 0 && (
        <ul className="mb-4 space-y-1 rounded border border-line bg-surface px-3 py-2 text-sm text-muted">{warnings.map((w) => <li key={w}>{w}</li>)}</ul>)}

      {!report ? (
        <div className="space-y-4">
          <section className="rounded-md border border-line bg-surface p-6">
            <h2 className="text-base font-semibold">No report for {exchange?.name ?? "this exchange"} yet</h2>
            <p className="mt-1 text-sm text-muted">Run the pipeline to collect posts, news and prices. Choose “Demo (synthetic)” to see the dashboard without any API keys.</p>
          </section>
          <RunStatusPanel run={run} report={null} exchange={exchange} />
        </div>
      ) : (
        <div className="space-y-4">
          <Rankings rows={report.rankings} selected={selected} onSelect={setSelected} />
          {asset && (
            <div>
              <h2 className="mb-2 text-lg font-semibold">{asset.name} <span className="text-sm font-normal text-muted">{asset.symbol}</span></h2>
              <AssetDetail a={asset} currency={report.exchange.currency} />
            </div>)}
          <div className="grid gap-4 lg:grid-cols-2">
            <ModelPanel report={report} />
            <RunStatusPanel run={run} report={report} exchange={exchange} />
          </div>
          <p className="text-xs text-muted">{report.disclaimer} Generated {report.generated_at.slice(0, 16).replace("T", " ")} UTC · window {report.run.lookback_days} days.</p>
        </div>)}
    </div>
  );
}
