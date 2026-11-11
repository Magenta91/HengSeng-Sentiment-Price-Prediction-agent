import type { Exchange, Report, RunStatus } from "./types";

const BASE = import.meta.env.VITE_API_URL ?? "";

async function request<T>(path: string, init?: RequestInit, allow404 = false): Promise<T | null> {
  const res = await fetch(`${BASE}${path}`, { headers: { "Content-Type": "application/json" }, ...init });
  if (allow404 && res.status === 404) return null;
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(typeof detail.detail === "string" ? detail.detail : `Request failed (${res.status})`);
  }
  return (await res.json()) as T;
}

export const api = {
  exchanges: () => request<Exchange[]>("/api/assets") as Promise<Exchange[]>,
  report: (exchange: string, mode: string) => request<Report>(`/api/reports/latest?exchange=${exchange}&mode=${mode}`, undefined, true),
  startRun: (exchange: string, mode: string) =>
    request<{ run_id: string }>("/api/pipeline/runs", { method: "POST", body: JSON.stringify({ exchange, mode }) }) as Promise<{ run_id: string }>,
  run: (id: string) => request<RunStatus>(`/api/pipeline/runs/${id}`) as Promise<RunStatus>,
};
