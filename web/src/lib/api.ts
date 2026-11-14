// Types mirror filingLens.views / filingLens.api.app (see /openapi.json).

export type ColumnType = 'period' | 'number' | 'percent' | 'change' | 'text' | 'integer'
export type Cell = string | number | null

export interface Column { key: string; label: string; type: ColumnType; unit: string | null }
export interface Table { columns: Column[]; rows: Record<string, Cell>[]; display: Record<string, string>[] }
export interface Point { period: string; value: number | null }
export interface Series { key: string; label: string; ticker: string | null; metric: string | null; points: Point[] }
export interface Chart { type: 'line' | 'bar' | 'grouped_bar'; title: string; unit: string; y_label: string; series: Series[] }
export interface Stat {
  kind: 'value' | 'cagr' | 'change' | 'change_pp'; label: string; value: number | null; unit: string
  display: string; series: string | null; period_from: string | null; period_to: string | null
}
export interface Source {
  kind: 'xbrl' | 'derived' | 'report'; label: string; ticker: string | null; metric: string | null
  period: string | null; xbrl_tag: string | null; doc_id: string | null; document: string | null
  page: number | null; section: string | null; verified: string | null
}
export interface Passage { rank: number; ticker: string | null; page: number | null; section: string | null; source: string; text: string }
export interface DataView {
  tool: string; title: string; unit: string; table: Table | null; chart: Chart | null; stats: Stat[]
  passages: Passage[]; sources: Source[]; notes: string[]; clarify: string | null
}
export interface AnswerView {
  question: string; ok: boolean; mode: 'llm' | 'rules' | 'search'; tool: string | null
  args: Record<string, unknown> | null; answer: string | null; answer_rejected: string | null
  data: DataView | null; notes: string[]; errors: string[]; seconds: number
}
export interface CompanyInfo {
  ticker: string; name: string; sector: string; fys: string[]; latest_fy: string | null
  latest_quarter: string | null; annual_reports: string[]; has_data: boolean
}
export interface MetricInfo { id: string; label: string; kind: 'flow' | 'stock' | 'ratio'; unit: string }
export interface Health { status: 'ok'; facts: number; companies_with_data: number; chunks: number; vector_index: boolean; llm: string | null }

const BASE: string = import.meta.env.VITE_API ?? (import.meta.env.DEV ? '/api' : '')

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) { super(message); this.status = status }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(BASE + path, init)
  } catch {
    throw new ApiError(0, 'Cannot reach the filingLens API. Is `filinglens serve` running on port 8765?')
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') msg = body.detail
    } catch { /* keep the status line */ }
    throw new ApiError(res.status, msg)
  }
  return res.json() as Promise<T>
}

type Q = Record<string, string | number | string[] | undefined | null>
function qs(params: Q): string {
  const u = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v == null) continue
    if (Array.isArray(v)) v.forEach((x) => u.append(k, x))
    else u.append(k, String(v))
  }
  return u.toString()
}

// GETs are cached for the session: the data only changes on ingest.
const cache = new Map<string, Promise<unknown>>()
function get<T>(path: string): Promise<T> {
  if (!cache.has(path)) {
    const p = request<T>(path)
    p.catch(() => cache.delete(path))
    cache.set(path, p)
  }
  return cache.get(path) as Promise<T>
}

export const api = {
  ask: (question: string, useLlm: boolean, signal?: AbortSignal) =>
    request<AnswerView>('/ask', {
      method: 'POST', headers: { 'content-type': 'application/json' }, signal,
      body: JSON.stringify({ question, use_llm: useLlm, write_answer: useLlm }),
    }),
  health: () => request<Health>('/health'),
  companies: () => get<CompanyInfo[]>('/companies'),
  metrics: () => get<MetricInfo[]>('/metrics'),
  trend: (p: { ticker: string; metric: string; last_n_years?: number; from_fy?: string; to_fy?: string; period?: 'FY' | 'Q' }) =>
    get<DataView>('/trend?' + qs(p)),
  compare: (p: { tickers: string[]; metrics: string[]; fys?: string[]; last_n_years?: number; normalise?: 'none' | 'index100' | 'share' }) =>
    get<DataView>('/compare?' + qs(p)),
}
