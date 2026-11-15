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
  question: string; ok: boolean; mode: 'llm' | 'rules' | 'search' | 'explain'; tool: string | null
  args: Record<string, unknown> | null; answer: string | null; answer_rejected: string | null
  data: DataView | null
  figures: DataView | null              // "why" questions: the figures behind the report passages
  notes: string[]; errors: string[]; seconds: number
}
export interface CompanyInfo {
  ticker: string; name: string; sector: string; fys: string[]; latest_fy: string | null
  latest_quarter: string | null; annual_reports: string[]; has_data: boolean
}
export interface MetricInfo { id: string; label: string; kind: 'flow' | 'stock' | 'ratio'; unit: string }
export type Role = 'admin' | 'user'
export interface User { id: string; email: string; role: Role; is_active: boolean; is_superuser: boolean; is_verified: boolean }
export interface Health { status: 'ok'; facts: number; companies_with_data: number; chunks: number; vector_index: boolean; llm: string | null }

const BASE: string = import.meta.env.VITE_API ?? (import.meta.env.DEV ? '/api' : '')

// admin (role "admin"): see filingLens.api.admin
export interface ListedRow {
  symbol: string; name: string; series: string; isin: string; listed_on: string | null; tracked: boolean
  xbrl_files: number; fys: string[]; latest_quarter: string | null; annual_reports: string[]; passages: number
  embedded: number                                    // passages with a vector (semantic search)
  reports: ReportDoc[]                                // each ingested annual-report PDF
  stored: number                                      // files in the S3 bucket
  pending_xbrl: number; pending_reports: number      // in the inbox, not ingested yet
}
export interface ReportDoc { doc_id: string; fy: string | null; passages: number; embedded: number }
export interface IndexInfo { model: string | null; passages: number; embedded: number; reports_pending: number; companies_pending: number }
export interface ListedCompanies { total: number; list_updated: string | null; storage: boolean; index: IndexInfo; rows: ListedRow[] }
export type EmbedDevice = 'auto' | 'cpu' | 'cuda'
export interface EmbedItem { doc_id: string; ticker: string; fy: string | null; passages: number; done: number; state: 'waiting' | 'running' | 'done' | 'stopped' | 'failed' }
export interface EmbedJob {
  state: 'loading' | 'running' | 'done' | 'stopped' | 'failed'; device: string | null; model: string | null
  items: EmbedItem[]; embedded: number; message: string; error: string | null
  started_at: string; finished_at: string | null; stop_requested: boolean
}
export type JobStage = 'queued' | 'listing' | 'downloading' | 'ingesting' | 'done' | 'failed'
export interface Job {
  symbol: string; name: string; stage: JobStage; done: number; total: number; downloaded: number; stored: number
  failed_files: number; added_company: boolean; message: string; error: string | null; queued_at: string
  started_at: string | null; finished_at: string | null; facts: number | null; years: string[]; passages: number | null
}
export interface StoredFile { key: string; name: string; kind: string; size: number; modified: string; url: string }
export interface NSEFile { kind: 'xbrl' | 'annual_report'; period: string; fy: string | null; scope: string | null; status: 'have' | 'new' }
export interface NSEAvailability {
  symbol: string; checked_at: string; xbrl_filings: number; full_years: string[]; quarters: string[]
  annual_reports: string[]; new_files: number; files: NSEFile[]; warning: string | null
}

/** Fired when the API answers 401 (session missing or expired); the auth layer signs out. */
export const UNAUTHORIZED = 'filinglens:unauthorized'

/** fastapi-users error codes -> what to tell the person. */
const AUTH_MESSAGES: Record<string, string> = {
  LOGIN_BAD_CREDENTIALS: 'Wrong email or password.',
  LOGIN_USER_NOT_VERIFIED: 'This account is not verified yet.',
  REGISTER_USER_ALREADY_EXISTS: 'An account with this email already exists. Sign in instead.',
}

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
    if (res.status === 401 && !path.startsWith('/auth/')) window.dispatchEvent(new Event(UNAUTHORIZED))
    let msg = `${res.status} ${res.statusText}`
    try {
      const { detail } = await res.json()
      if (typeof detail === 'string') msg = AUTH_MESSAGES[detail] ?? detail
      else if (detail?.reason) msg = detail.reason                       // invalid password
      else if (Array.isArray(detail) && detail[0]?.msg) msg = detail[0].msg  // validation error
    } catch { /* keep the status line */ }
    throw new ApiError(res.status, msg)
  }
  if (res.status === 204) return undefined as T
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

/** Forget cached data (on sign-in / sign-out, so one account never sees another's cache). */
export function clearCache() { cache.clear() }

export const auth = {
  me: () => request<User>('/auth/me'),
  login: (email: string, password: string) =>
    request<void>('/auth/login', {
      method: 'POST', headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ username: email, password }),
    }),
  register: (email: string, password: string) =>
    request<User>('/auth/register', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email, password }),
    }),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
}

export const admin = {
  companies: () => request<ListedCompanies>('/admin/companies'),
  download: (symbol: string) => request<Job>(`/admin/companies/${encodeURIComponent(symbol)}/download`, { method: 'POST' }),
  jobs: () => request<Job[]>('/admin/jobs'),
  files: (symbol: string) => request<StoredFile[]>(`/admin/companies/${encodeURIComponent(symbol)}/files`),
  embedJob: () => request<EmbedJob | null>('/admin/embeddings'),
  embed: (body: { doc_ids?: string[]; tickers?: string[]; device: EmbedDevice }) =>
    request<EmbedJob>('/admin/embeddings', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }),
  stopEmbed: () => request<EmbedJob>('/admin/embeddings', { method: 'DELETE' }),
  nse: (symbol: string, refresh = false, signal?: AbortSignal) =>
    request<NSEAvailability>(`/admin/companies/${encodeURIComponent(symbol)}/nse${refresh ? '?refresh=true' : ''}`, { signal }),
}

export const api = {
  /** ``previous``: the last answer, so a short follow-up ("Wipro", "and Infosys?") is read with it. */
  ask: (question: string, useLlm: boolean, signal?: AbortSignal, previous?: { question: string; tool: string | null }) =>
    request<AnswerView>('/ask', {
      method: 'POST', headers: { 'content-type': 'application/json' }, signal,
      body: JSON.stringify({ question, use_llm: useLlm, write_answer: useLlm, previous: previous ?? null }),
    }),
  health: () => request<Health>('/health'),
  companies: () => get<CompanyInfo[]>('/companies'),
  metrics: () => get<MetricInfo[]>('/metrics'),
  trend: (p: { ticker: string; metric: string; last_n_years?: number; from_fy?: string; to_fy?: string; period?: 'FY' | 'Q' }) =>
    get<DataView>('/trend?' + qs(p)),
  compare: (p: { tickers: string[]; metrics: string[]; fys?: string[]; last_n_years?: number; normalise?: 'none' | 'index100' | 'share' }) =>
    get<DataView>('/compare?' + qs(p)),
}
