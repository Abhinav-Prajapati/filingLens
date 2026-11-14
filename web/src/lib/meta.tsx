import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, type CompanyInfo, type Health, type MetricInfo } from './api'
import { humanise } from './format'
import { setCompanyOrder } from './theme'

interface Meta {
  companies: CompanyInfo[]            // every registered company, alphabetical
  loaded: CompanyInfo[]               // those with data
  metrics: MetricInfo[]
  health: Health | null
  error: string | null
  ready: boolean
  metricLabel: (id: string | null | undefined) => string
  companyName: (ticker: string | null | undefined) => string
  retry: () => void
}

const Ctx = createContext<Meta | null>(null)

export function MetaProvider({ children }: { children: ReactNode }) {
  const [companies, setCompanies] = useState<CompanyInfo[]>([])
  const [metrics, setMetrics] = useState<MetricInfo[]>([])
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [ready, setReady] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let live = true
    Promise.all([api.companies(), api.metrics(), api.health()])
      .then(([c, m, h]) => {
        if (!live) return
        setCompanyOrder(c.map((x) => x.ticker))     // colour slot = position in the registry
        setCompanies(c); setMetrics(m); setHealth(h); setError(null); setReady(true)
      })
      .catch((e: Error) => { if (live) { setError(e.message); setReady(true) } })
    return () => { live = false }
  }, [attempt])

  const value = useMemo<Meta>(() => {
    const labels = Object.fromEntries(metrics.map((m) => [m.id, m.label]))
    const names = Object.fromEntries(companies.map((c) => [c.ticker, c.name]))
    return {
      companies, metrics, health, error, ready,
      loaded: companies.filter((c) => c.has_data),
      metricLabel: (id) => (id ? labels[id] ?? humanise(id) : ''),
      companyName: (t) => (t ? names[t] ?? t : ''),
      retry: () => { setReady(false); setAttempt((a) => a + 1) },
    }
  }, [companies, metrics, health, error, ready])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useMeta(): Meta {
  const m = useContext(Ctx)
  if (!m) throw new Error('useMeta outside MetaProvider')
  return m
}

/** Short display name: "Hindustan Unilever" -> keeps; "Tata Consultancy Services" -> "TCS". */
export function shortName(c: { ticker: string; name: string }): string {
  if (c.name.length <= 18) return c.name
  return c.ticker
}
