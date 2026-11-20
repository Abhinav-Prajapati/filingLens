import { useSyncExternalStore } from 'react'
import { theme } from 'antd'

export type ThemePref = 'light' | 'dark' | 'system'

/** Chart colours, all read from Ant Design's resolved tokens: surfaces and text from the
 *  neutral tokens, up/down from the success/error tokens, and the categorical series from the
 *  preset palette, which Ant Design reserves for charts and tags. */
export interface Tokens {
  dark: boolean
  surface: string; surface2: string; ink: string; ink2: string; muted: string
  grid: string; axis: string; rule: string; up: string; down: string
  series: string[]                  // categorical slots, fixed order
  divNeg: string; divMid: string; divPos: string; seqLo: string; seqHi: string
  sans: string; mono: string
  radius: number; shadow: string
  onSolid: string                   // text on a saturated fill
}

// ---- theme preference (stored per viewer; the page works without storage) ----------------
const KEY = 'filinglens.theme'
const listeners = new Set<() => void>()
let pref: ThemePref = (() => {
  try { return (localStorage.getItem(KEY) as ThemePref) || 'dark' } catch { return 'dark' }   // dark by default
})()
// ?theme=dark|light|system in the URL sets the preference (handy for links and screenshots)
try {
  const q = new URLSearchParams(location.search).get('theme') as ThemePref | null
  if (q === 'dark' || q === 'light' || q === 'system') pref = q
} catch { /* no location */ }

export function setThemePref(p: ThemePref) {
  pref = p
  try { localStorage.setItem(KEY, p) } catch { /* private mode */ }
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

/** Chart tokens from the surrounding ConfigProvider (so they follow light/dark). */
export function useTokens(): Tokens {
  const { token } = theme.useToken()
  const dark = useIsDark()
  return {
    dark,
    surface: token.colorBgElevated, surface2: token.colorFillAlter,
    ink: token.colorText, ink2: token.colorTextSecondary, muted: token.colorTextTertiary,
    grid: token.colorSplit, axis: token.colorBorder, rule: token.colorBorderSecondary,
    up: token.colorSuccess, down: token.colorError,
    // preset palette, in an order that keeps neighbours distinct
    series: [token.blue, token.green, token.orange, token.purple, token.cyan, token.magenta, token.gold, token.geekblue],
    divNeg: token.colorError, divMid: token.colorFillSecondary, divPos: token.colorPrimary,
    seqLo: token.colorPrimaryBg, seqHi: token.colorPrimary,
    sans: token.fontFamily, mono: token.fontFamily,       // Ant Design: one UI font; code font is for code
    radius: token.borderRadiusLG, shadow: token.boxShadowSecondary,
    onSolid: token.colorTextLightSolid,
  }
}

// ---- entity colours: a company keeps its colour everywhere, whatever is filtered ----------
let order: string[] = []
export function setCompanyOrder(tickers: string[]) { order = tickers }

export function companySlot(ticker: string | null | undefined): number {
  if (!ticker) return 0
  const i = order.indexOf(ticker)
  return i >= 0 ? i % 8 : 0
}

/** Colour slot of `ticker` among the companies drawn together. Each keeps its registry slot
 *  (position % 8) unless an earlier company in the set already holds it; then it takes the
 *  lowest free slot, so no two companies in one chart share a colour (up to 8). */
export function slotIn(ticker: string, set: readonly string[]): number {
  const members = [...new Set(set)].filter((x) => order.includes(x)).sort((a, b) => order.indexOf(a) - order.indexOf(b))
  const taken = new Set<number>()
  for (const m of members) {
    let s = companySlot(m)
    if (taken.has(s)) s = [0, 1, 2, 3, 4, 5, 6, 7].find((x) => !taken.has(x)) ?? s
    taken.add(s)
    if (m === ticker) return s
  }
  return companySlot(ticker)
}

/** A company's colour; pass `set` (the companies in the same chart) to keep colours distinct. */
export function seriesColor(t: Tokens, key: string | null | undefined, fallbackIndex: number, set?: readonly string[]): string {
  if (key && order.includes(key)) return t.series[set ? slotIn(key, set) : companySlot(key)]
  return t.series[fallbackIndex % 8]
}
