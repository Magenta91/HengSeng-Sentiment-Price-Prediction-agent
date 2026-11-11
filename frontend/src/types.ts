export interface Source { name: string; operational: boolean; problem: string | null }
export interface AssetInfo { symbol: string; name: string; exchange: string; currency: string }
export interface Exchange { code: string; name: string; timezone: string; currency: string; assets: AssetInfo[]; sources: Source[] }

export interface Ranking {
  symbol: string; name: string; social_mentions: number; mention_rank: number | null;
  sentiment_score: number | null; sentiment_rank: number | null;
  direction: "up" | "down" | "mixed" | "sideways" | "unclear" | null; direction_net: number | null; direction_rank: number | null;
  news_articles: number; news_sentiment: number | null;
}
export interface Position { type: "long" | "short" | null; quantity: number | null; entry_price: number | null; evidence_text: string | null }
export interface Intel {
  source: string; source_id: string; source_type: string; sentiment: string; sentiment_score: number; direction: string;
  position: Position | null; support_levels: number[]; resistance_levels: number[]; target_price: number | null;
  confidence: number; evidence_text: string | null; timestamp: string | null;
}
export interface HistoryPoint {
  as_of_date: string; target_session_date: string; predicted_return: number; predicted_price: number;
  actual_price: number | null; actual_return: number | null;
}
export interface DailyPoint { date: string; social_mentions?: number; social_sentiment?: number | null; news_mentions?: number; news_sentiment?: number | null }
export interface AssetReport {
  symbol: string; name: string;
  price: { current: number | null; as_of_date: string | null; currency: string };
  prediction: { predicted_price: number; predicted_return: number; target_session_date: string; model_version: string } | null;
  prediction_note: string | null;
  sentiment: {
    social: { mention_count?: number; scored_count?: number; sentiment_mean?: number | null; weighted_sentiment?: number | null; unique_author_count?: number };
    distribution: { bullish: number | null; bearish: number | null; neutral: number | null };
    news: { mention_count?: number; sentiment_mean?: number | null };
  };
  by_source: { source: string; mention_count: number; scored_count: number; sentiment_mean: number | null }[];
  daily: DailyPoint[];
  coverage: { social_posts: number; social_scored: number; news_articles: number; market_bars: number; extractions: number };
  news: { title: string; url: string; published_at: string | null; source_domain: string | null; sentiment_score: number | null }[];
  intelligence: Intel[];
  technical_levels: { method: string; calculated: boolean; support: number; resistance: number } | null;
  history: HistoryPoint[];
}
export interface WorkerOutcome { name: string; status: string; attempts: number; duration_s: number; error: string | null; reason: string | null }
export interface Metrics { n: number; mae: number | null; rmse: number | null; directional_accuracy: number | null }
export interface Report {
  is_demo: boolean; generated_at: string; disclaimer: string; warnings: string[];
  run: { run_id: string; mode: string; exchange_code: string; window_start: string; window_end: string; lookback_days: number };
  exchange: { code: string; name: string; timezone: string; currency: string };
  rankings: Ranking[]; assets: AssetReport[];
  model: { available: boolean; reason?: string; model_name?: string; model_version?: string; feature_set?: string; demo?: boolean;
           date_ranges?: Record<string, string[]>; metrics?: { test?: { model: Metrics; baseline_zero_return: Metrics; mae_improvement_pct: number | null } } };
  evaluation: { n: number; model?: Metrics; baseline_zero_return?: Metrics; mae_improvement_pct?: number | null } | null;
  backtest: { status: string; reason?: string; overall?: { model: Metrics; baseline_zero_return: Metrics; mae_improvement_pct: number | null } } | null;
  workers: WorkerOutcome[];
}
export interface RunStatus {
  run_id: string; status: string | null; error: string | null; warnings: string[];
  workers: { worker_name: string; status: string | null; attempts: number | null; error: string | null }[];
}
