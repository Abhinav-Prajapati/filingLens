import { useEffect, useState, useSyncExternalStore } from 'react'

export type ThemePref = 'light' | 'dark' | 'system'

export interface Tokens {
  dark: boolean
  surface: string; surface2: string; ink: string; ink2: string; muted: string
  grid: string; axis: string; rule: string; up: string; down: string
  series: string[]                  // categorical slots 1..8, fixed order
  divNeg: string; divMid: string; divPos: string; seqLo: string; seqHi: string
  sans: string; mono: string
}

function read(): Tokens {
  const cs = getComputedStyle(document.documentElement)
  const v = (n: string) => cs.getPropertyValue(n).trim()
  return {
    dark: cs.colorScheme === 'dark',
    surface: v('--surface'), surface2: v('--surface-2'), ink: v('--ink'), ink2: v('--ink-2'),
    muted: v('--muted'), grid: v('--grid'), axis: v('--axis'), rule: v('--rule'),
    up: v('--up'), down: v('--down'),
    series: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => v(`--s${i}`)),
    divNeg: v('--div-neg'), divMid: v('--div-mid'), divPos: v('--div-pos'),
    seqLo: v('--seq-lo'), seqHi: v('--seq-hi'),
    sans: v('--sans'), mono: v('--mono'),
  }
}

// ---- theme preference (stored per viewer; the page works without storage) ----------------
const KEY = 'filinglens.theme'
const listeners = new Set<() => void>()
let pref: ThemePref = (() => {
  try { return (localStorage.getItem(KEY) as ThemePref) || 'dark' } catch { return 'dark' }   // dark by default
})()

function apply(p: ThemePref) {
  const root = document.documentElement
  if (p === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', p)
}
// ?theme=dark|light|system in the URL sets the preference (handy for links and screenshots)
try {
  const q = new URLSearchParams(location.search).get('theme') as ThemePref | null
  if (q === 'dark' || q === 'light' || q === 'system') pref = q
} catch { /* no location */ }
apply(pref)

export function setThemePref(p: ThemePref) {
  pref = p
  try { localStorage.setItem(KEY, p) } catch { /* private mode */ }
  apply(p)
  listeners.forEach((l) => l())
}

export function useThemePref(): ThemePref {
  return useSyncExternalStore((cb) => { listeners.add(cb); return () => listeners.delete(cb) }, () => pref)
}

const darkMq = () => window.matchMedia('(prefers-color-scheme: dark)')

/** Whether the page is currently dark (the preference, or the OS when it is "system"). */
export function useIsDark(): boolean {
  const p = useThemePref()
  const os = useSyncExternalStore(
    (cb) => { const mq = darkMq(); mq.addEventListener('change', cb); return () => mq.removeEventListener('change', cb) },
    () => darkMq().matches,
  )
  return p === 'dark' || (p === 'system' && os)
}

/** Resolved colour tokens; changes when the toggle or the OS theme changes. */
export function useTokens(): Tokens {
  const p = useThemePref()
  const [tokens, setTokens] = useState<Tokens>(read)
  useEffect(() => {
    setTokens(read())
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const on = () => setTokens(read())
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [p])
  return tokens
}

// ---- entity colours: a company keeps its colour everywhere, whatever is filtered ----------
let order: string[] = []
export function setCompanyOrder(tickers: string[]) { order = tickers }

export function companySlot(ticker: string | null | undefined): number {
  if (!ticker) return 0
  const i = order.indexOf(ticker)
  return i >= 0 ? i % 8 : 0
}

export function seriesColor(t: Tokens, key: string | null | undefined, fallbackIndex: number): string {
  if (key && order.includes(key)) return t.series[companySlot(key)]
  return t.series[fallbackIndex % 8]
}
