// Indian number formatting. Amounts arrive in INR crore; 1 lakh crore = 1,00,000 crore.

const IN = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 })
const IN2 = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2, minimumFractionDigits: 2 })

export const isPctUnit = (unit: string | null | undefined) => !!unit && unit.startsWith('%')
export const isIndexUnit = (unit: string | null | undefined) => !!unit && unit.startsWith('index')
export const isPerShare = (unit: string | null | undefined) => unit === 'INR' || unit === 'INR per share'

/** Full figure, e.g. "2,55,324" / "18.52%" / "₹ 132.41". */
export function fmtValue(v: number | null | undefined, unit: string | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '–'
  if (isPctUnit(unit)) return `${v.toFixed(2)}%`
  if (isIndexUnit(unit)) return v.toFixed(1)
  if (isPerShare(unit)) return `₹${IN2.format(v)}`
  return IN.format(v)
}

/** Compact figure for axes and tiles: "2.55 L Cr", "64,468 Cr", "18.5%". */
export function fmtCompact(v: number | null | undefined, unit: string | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '–'
  if (isPctUnit(unit)) return `${trim(v.toFixed(1))}%`
  if (isIndexUnit(unit)) return trim(v.toFixed(0))
  if (isPerShare(unit)) return `₹${trim(v.toFixed(v >= 100 ? 0 : 1))}`
  const a = Math.abs(v)
  if (a >= 1e5) return `${trim((v / 1e5).toFixed(2))}L Cr`
  return `${IN.format(v)} Cr`
}

/** Axis tick: clean, one style per axis. */
export function fmtAxis(v: number, unit: string | null | undefined): string {
  if (isPctUnit(unit)) return `${trim(v.toFixed(1))}%`
  if (isIndexUnit(unit) || isPerShare(unit)) return trim(v.toFixed(0))
  const a = Math.abs(v)
  if (a >= 1e5) return `${trim((v / 1e5).toFixed(1))}L`
  if (a >= 1e3) return `${trim((v / 1e3).toFixed(0))}K`
  return IN.format(v)
}

export function fmtChange(v: number | null | undefined, unit: 'pp' | '%' = '%'): string {
  if (v == null || Number.isNaN(v)) return '–'
  const s = v > 0 ? '+' : v < 0 ? '−' : '±'
  return `${s}${Math.abs(v).toFixed(unit === 'pp' ? 2 : 1)}${unit === 'pp' ? ' pp' : '%'}`
}

/** Human unit line under a figure. */
export function unitLabel(unit: string | null | undefined): string {
  if (!unit) return ''
  if (unit === 'INR crore') return '₹ crore'
  if (isPerShare(unit)) return '₹ per share'
  if (unit === '% of total') return 'share of total'
  return unit
}

function trim(s: string): string {
  return s.includes('.') ? s.replace(/\.?0+$/, '') : s
}

/** "revenue_from_operations" -> "Revenue from operations" when the API gives only an id. */
export function humanise(id: string): string {
  const s = id.replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/** "INFY net_profit (consolidated)" -> "INFY · Net profit · consolidated" using metric labels. */
export function prettyTitle(title: string, labels: Record<string, string>): string {
  return title.replace(/\b[a-z]+(?:_[a-z]+)+\b|\b(net_margin|pbt_margin)\b/g, (m) => labels[m] ?? humanise(m))
}
