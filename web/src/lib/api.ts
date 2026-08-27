import { pushToast } from './toast';

/* ------------------------------------------------------------------ */
/* Error handling                                                      */
/* ------------------------------------------------------------------ */

export class ApiError extends Error {
  status: number;
  constructor(message: string, status = 0) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

const BASE = '/api';

// Debounce repeated "backend unreachable" toasts.
let connWarnedAt = 0;

async function detailOf(res: Response): Promise<string> {
  try {
    const data: unknown = await res.json();
    if (data && typeof data === 'object' && 'detail' in data) {
      const d = (data as { detail: unknown }).detail;
      if (typeof d === 'string' && d) return d;
      return JSON.stringify(d);
    }
    return JSON.stringify(data);
  } catch {
    return `HTTP ${res.status} ${res.statusText}`;
  }
}

function netFail(): ApiError {
  const now = Date.now();
  if (now - connWarnedAt > 30_000) {
    connWarnedAt = now;
    pushToast('error', '无法连接后端服务 — 请先在 8300 端口运行 investlab serve', 8000);
  }
  return new ApiError('网络请求失败：后端服务不可达（127.0.0.1:8300）');
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    const cfg: RequestInit = { ...init };
    if (typeof cfg.body === 'string') {
      cfg.headers = { 'Content-Type': 'application/json' };
    }
    res = await fetch(BASE + path, cfg);
  } catch {
    throw netFail();
  }
  if (!res.ok) throw new ApiError(await detailOf(res), res.status);
  return (await res.json()) as T;
}

/* ------------------------------------------------------------------ */
/* Types mirroring the FastAPI contract                                */
/* ------------------------------------------------------------------ */

export interface SettingsStatus {
  version: string;
  tokens: Partial<Record<'llm' | 'vision' | 'tushare' | 'fred' | 'tavily', string>>;
  vault_path: string;
  data_dir: string;
  mode: string;
}

export interface BriefingMacroItem {
  pmi?: number | null;
  cpi_yoy?: number | null;
  gdp_yoy?: number | null;
  lpr_1y?: number | null;
  text?: string | null;
  sectors?: { name: string; change_pct: number }[] | null;
}

export interface BriefingPosition {
  symbol: string;
  name?: string | null;
  quantity?: number | null;
  cost_price?: number | null;
  price?: number | null;
  change_pct?: number | null;
  currency?: string | null;
}

export interface BriefingNews {
  title?: string | null;
  url?: string | null;
  source?: string | null;
  published?: string | null;
}

export interface BriefingNews {
  title?: string | null;
  url?: string | null;
  link?: string | null;
  source?: string | null;
  published?: string | null;
}

export interface BriefingNewsGroup {
  fresh?: BriefingNews[];
  background?: BriefingNews[];
  by_symbol?: Record<string, BriefingNews[]>;
  error?: string;
}

export interface Briefing {
  date: string;
  macro?: BriefingMacroItem | null;
  macro_text?: string | null;
  positions?: BriefingPosition[] | null;
  sectors?: { name: string; change_pct: number }[] | null;
  news?: BriefingNewsGroup | (BriefingNews | string)[] | null;
  obsidian_note?: string | null;
  briefing_json?: string | null;
  generated_at?: string | null;
}

export interface BriefingsResp {
  dates: string[];
  latest: Briefing | null;
}

export interface BriefingRunResp {
  ok: boolean;
  date: string;
  obsidian_note?: string | null;
  briefing_json?: Briefing | null;
}

export interface PortfolioPosition {
  symbol: string;
  name: string;
  quantity: number;
  cost_price: number;
  currency: string;
  category: string;
  price: number;
  change_pct: number;
  pnl: number;
  pnl_pct: number;
  weight_pct: number;
  market_value: number;
  quote_source: string;
}

export interface PortfolioResp {
  positions: PortfolioPosition[];
  total_value_cny: number;
  total_pnl_cny: number;
  skipped: string[];
}

export interface PortfolioRowInput {
  symbol: string;
  name?: string;
  quantity: number;
  cost_price: number;
}

export interface PortfolioSaveResp {
  ok: boolean;
  saved_to: string;
}

export interface MacroResp {
  items?: {
    pmi?: number | null;
    cpi_yoy?: number | null;
    gdp_yoy?: number | null;
    lpr_1y?: number | null;
  };
  sectors?: { name: string; change_pct: number }[];
  text: string;
}

export interface SignalRule {
  name: string;
  direction: number;
  weight: number;
  reason: string;
}

export type IndicatorValue = number | string | boolean | null;

export interface Signal {
  symbol: string;
  score: number;
  stance: string;
  rules: SignalRule[];
  indicators: Record<string, IndicatorValue>;
  gaps: string[];
  ts?: string | null;
}

export interface TrackRef {
  tier: string;
  id: string;
  name: string;
  maturity?: string | null;
}

export interface ResearchFacts {
  quote?: {
    name?: string | null;
    price?: number | null;
    change_pct?: number | null;
    currency?: string | null;
  } | null;
  technical?: Record<string, unknown> | null;
  fundamentals?: Record<string, unknown> | null;
  tracks?: TrackRef[];
}

export interface PositionAdvice {
  stance: string;
  entry_zone: string;
  invalidation: string;
}

export interface Verdict {
  headline: string;
  bull_case: string[];
  bear_case: string[];
  position_advice: PositionAdvice;
  thesis_fit?: { fits: boolean; comment: string } | null;
  confidence: number;
}

export interface ResearchResult {
  symbol: string;
  facts: ResearchFacts;
  verdict: Verdict;
  obsidian_note?: string | null;
}

export type BacktestStrategy = 'sma_cross' | 'rsi_reversion' | 'breakout_20';

export interface BacktestReq {
  symbol: string;
  strategy: BacktestStrategy;
  days: number;
}

export interface BacktestMetrics {
  total_return_pct: number;
  bench_return_pct: number;
  cagr_pct: number;
  sharpe: number;
  max_drawdown_pct: number;
  win_rate_pct: number;
  trades: number;
}

export interface BacktestPoint {
  date: string;
  strategy: number;
  benchmark: number;
}

export interface BacktestTrade {
  entry: unknown;
  exit: unknown;
  pnl: number;
}

export interface BacktestResp {
  ok: boolean;
  metrics?: BacktestMetrics;
  curve?: BacktestPoint[];
  trade_list?: BacktestTrade[];
  error?: string;
}

export interface LensTrack {
  id: string;
  name: string;
  tam?: string | null;
  cagr?: string | null;
  maturity?: string | null;
}

export interface LensRankRow {
  rank: number;
  track: string;
  id: string;
  mode?: string | null;
}

export interface LensesResp {
  lenses: Record<string, LensTrack[]>;
  top_ranking: LensRankRow[];
  verification_metrics: string[];
}

export interface TrackRow {
  symbol: string;
  name: string;
  price: number;
  change_pct: number;
  score: number | null;
  stance: string;
  gaps?: string[] | null;
}

export interface TrackDetail {
  ok: boolean;
  track: {
    id: string;
    name: string;
    tier?: string | null;
    tam?: string | null;
    maturity?: string | null;
  };
  rows: TrackRow[];
}

export interface Taxonomy {
  thesis?: string;
  allocation_frame?: Record<string, unknown>;
  secondary_tracks?: unknown[];
  tertiary_tracks?: unknown[];
  [key: string]: unknown;
}

export interface SearchHit {
  title: string;
  url: string;
  snippet?: string | null;
  badge?: string | null;
  provider?: string | null;
}

export interface SearchResp {
  query: string;
  hits: SearchHit[];
}

export interface ChatSource {
  title: string;
  url: string;
  domain?: string | null;
}

export interface ChatResp {
  answer: string;
  sources: ChatSource[];
}

export interface ReportSymbolRef {
  symbol: string;
  name?: string | null;
}

export interface ReportSummary {
  one_liner?: string | null;
  core_views?: string[] | null;
  investment_logic?: string[] | null;
  risks?: string[] | null;
  catalysts?: string[] | null;
}

export interface FigureMeta {
  file: string;
  classify: {
    type?: string | null;
    title?: string | null;
    takeaway?: string | null;
    series?: unknown;
    needs_manual_check?: boolean;
  };
}

export interface ReportRecord {
  id: string;
  filename: string;
  title?: string | null;
  broker?: string | null;
  date?: string | null;
  symbols?: ReportSymbolRef[] | null;
  rating?: string | null;
  target_price?: number | null;
  n_pages?: number | null;
  status: string;
  summary?: ReportSummary | null;
  vision_done?: boolean;
  figures_meta?: FigureMeta[] | null;
  obsidian_note?: string | null;
}

export interface ReportsResp {
  reports: ReportRecord[];
}

export interface UploadResp {
  ok: boolean;
  record: ReportRecord;
  next?: string | null;
}

export interface ParseResp {
  ok: boolean;
  record: ReportRecord;
  obsidian_note?: string | null;
}

export interface VaultItem {
  dir: string;
  name: string;
  is_dir: boolean;
  relpath: string;
}

export interface VaultTreeResp {
  root: string;
  items: VaultItem[];
}

/* ------------------------------------------------------------------ */
/* Typed wrappers                                                      */
/* ------------------------------------------------------------------ */

export const api = {
  health: (): Promise<{ ok: boolean }> => request('/health'),

  settingsStatus: (): Promise<SettingsStatus> => request('/settings/status'),

  briefings: (): Promise<BriefingsResp> => request('/briefings'),

  briefing: (date: string): Promise<Briefing> =>
    request(`/briefings/${encodeURIComponent(date)}`),

  runBriefing: (): Promise<BriefingRunResp> => request('/briefings/run', { method: 'POST' }),

  portfolio: (): Promise<PortfolioResp> => request('/portfolio'),

  savePortfolio: (rows: PortfolioRowInput[]): Promise<PortfolioSaveResp> =>
    request('/portfolio', { method: 'POST', body: JSON.stringify(rows) }),

  macro: (): Promise<MacroResp> => request('/macro'),

  signals: (symbol: string): Promise<Signal> =>
    request(`/signals/${encodeURIComponent(symbol)}`),

  researchAnalyze: (symbol: string): Promise<ResearchResult> =>
    request('/research/analyze', {
      method: 'POST',
      body: JSON.stringify({ symbol }),
    }),

  backtest: (req: BacktestReq): Promise<BacktestResp> =>
    request('/backtest', { method: 'POST', body: JSON.stringify(req) }),

  screenLenses: (): Promise<LensesResp> => request('/screen/lenses'),

  screenTrack: (trackId: string): Promise<TrackDetail> =>
    request(`/screen/track/${encodeURIComponent(trackId)}`),

  tracks: (): Promise<Taxonomy> => request('/tracks'),

  search: (query: string, maxResults = 8): Promise<SearchResp> =>
    request('/search', {
      method: 'POST',
      body: JSON.stringify({ query, max_results: maxResults }),
    }),

  chat: (question: string, useSearch = false): Promise<ChatResp> =>
    request('/chat', {
      method: 'POST',
      body: JSON.stringify({ question, use_search: useSearch }),
    }),

  reports: (): Promise<ReportsResp> => request('/reports'),

  uploadReport: async (file: File): Promise<UploadResp> => {
    const fd = new FormData();
    fd.append('file', file);
    let res: Response;
    try {
      res = await fetch(`${BASE}/reports/upload`, { method: 'POST', body: fd });
    } catch {
      throw netFail();
    }
    if (!res.ok) throw new ApiError(await detailOf(res), res.status);
    return (await res.json()) as UploadResp;
  },

  parseReport: (rid: string): Promise<ParseResp> =>
    request(`/reports/${encodeURIComponent(rid)}/parse`, { method: 'POST' }),

  /** Original PDF served by the backend. */
  reportPdfUrl: (rid: string): string => `${BASE}/reports/${encodeURIComponent(rid)}/pdf`,

  vaultTree: (): Promise<VaultTreeResp> => request('/vault/tree'),
};
