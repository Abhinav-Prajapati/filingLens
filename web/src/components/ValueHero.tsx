import { useEffect, useState } from 'react'
import { Tag } from 'antd'
import { TrophyOutlined } from '@ant-design/icons'
import { api, type DataView, type Stat } from '../lib/api'
import { fmtValue, isPctUnit, isPerShare, unitLabel } from '../lib/format'
import { useMeta } from '../lib/meta'
import { seriesColor, useTokens } from '../lib/theme'
import { EChart } from '../charts/EChart'
import { rankedOption, sparkOption } from '../charts/options'
import { CountUp, Delta } from './bits'

interface Args { ticker?: string; metric?: string; fy?: string; quarter?: string | null }

/** One number, large, with context fetched around it: its own history and where peers stand. */
export function ValueHero({ stat, args, goodWhenUp }: { stat: Stat; args: Args; goodWhenUp: boolean }) {
  const t = useTokens()
  const { loaded, metricLabel, companyName } = useMeta()
  const [trend, setTrend] = useState<DataView | null>(null)
  const [peers, setPeers] = useState<DataView | null>(null)
  const ticker = args.ticker ?? stat.series ?? ''
  const metric = args.metric ?? ''
  const period = stat.period_to ?? ''
  const fy = period.split(' ')[0]
  const quarterly = period.includes('Q')
  const color = seriesColor(t, ticker, 0)
  const unit = stat.unit

  useEffect(() => {
    if (!ticker || !metric) return
    let live = true
    api.trend({ ticker, metric, period: quarterly ? 'Q' : 'FY' }).then((d) => live && setTrend(d)).catch(() => {})
    const others = loaded.map((c) => c.ticker)
    if (!quarterly && others.length > 1 && !isPerShare(unit)) {
      api.compare({ tickers: others, metrics: [metric], fys: [fy] }).then((d) => live && setPeers(d)).catch(() => {})
    }
    return () => { live = false }
  }, [ticker, metric, fy, quarterly, loaded, unit])

  const pts = trend?.chart?.series[0]?.points ?? []
  const idx = pts.findIndex((p) => p.period === period)
  const prevPt = idx > 0 ? pts.slice(0, idx).reverse().find((p) => p.value != null) : undefined
  const change = prevPt?.value && stat.value != null
    ? isPctUnit(unit) ? stat.value - prevPt.value : ((stat.value - prevPt.value) / Math.abs(prevPt.value)) * 100
    : null
  const peerRow = peers?.table?.rows[0]
  const peerItems = peerRow
    ? loaded.map((c) => ({ label: c.ticker, value: (peerRow[c.ticker] as number | null) ?? null, color: seriesColor(t, c.ticker, 0), highlight: c.ticker === ticker }))
        .filter((p) => p.value != null)
    : []
  const rank = peerItems.length > 1 ? [...peerItems].sort((a, b) => (b.value as number) - (a.value as number)).findIndex((p) => p.highlight) + 1 : 0

  const v = stat.value ?? 0
  const pct = isPctUnit(unit)
  const per = isPerShare(unit)
  const fmtHero = (x: number) => (pct ? x.toFixed(2) : per ? x.toFixed(2) : new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(x))

  return (
    <div className="hero">
      <div className="hero-fig">
        <div className="eyebrow">{companyName(ticker)} · {period}</div>
        <div style={{ fontSize: 14, color: 'var(--ink-2)', marginTop: 2 }}>{metricLabel(metric)}</div>
        <div className="hero-num num" aria-label={fmtValue(stat.value, unit)}>
          {!pct && <span className="cur">₹</span>}
          {stat.value == null ? '–' : <CountUp value={v} format={fmtHero} duration={1.3} />}
          {pct && <span className="cur" style={{ marginLeft: 4, marginRight: 0 }}>%</span>}
        </div>
        <div className="hero-unit">
          {pct ? (metric.includes('margin') || metric.includes('ratio') ? 'of revenue' : '') : unitLabel(unit)}
          {!pct && !per && v >= 1e5 ? ` · ₹${(v / 1e5).toFixed(2)} lakh crore` : ''}
        </div>
        <div className="hero-meta">
          {change != null && prevPt && (
            <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
              <Delta value={change} unit={pct ? 'pp' : '%'} goodWhenUp={goodWhenUp} /> vs {prevPt.period}
            </span>
          )}
          {rank > 0 && <Tag icon={<TrophyOutlined />} variant="outlined" style={{ margin: 0 }}>#{rank} of {peerItems.length} covered companies</Tag>}
        </div>
      </div>
      <div className="hero-ctx">
        <div className="chart-box">
          <div className="eyebrow">History{quarterly ? ' · quarterly' : ''}</div>
          {pts.length > 1
            ? <EChart size="sm" option={sparkOption(t, pts, color, unit, period)} label={`${ticker} ${metricLabel(metric)} history`} />
            : <div className="chart sm" />}
        </div>
        {peerItems.length > 1 && (
          <div className="chart-box">
            <div className="eyebrow">Peers · {fy}</div>
            <EChart option={rankedOption(t, peerItems, unit)} height={peerItems.length * 34 + 12} label={`${metricLabel(metric)} across covered companies, ${fy}`} />
          </div>
        )}
      </div>
    </div>
  )
}
