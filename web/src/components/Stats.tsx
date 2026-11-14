import { Statistic, Tag } from 'antd'
import type { Stat } from '../lib/api'
import { useMeta } from '../lib/meta'
import { seriesColor, useTokens } from '../lib/theme'
import { CountUp, Rise } from './bits'

/** Headline changes of a trend or comparison: one tile per series (company or metric) with
 *  its total change and, for amounts, the CAGR. */
export function StatStrip({ stats, goodWhenUp }: { stats: Stat[]; goodWhenUp: boolean }) {
  const t = useTokens()
  const { companies, metricLabel } = useMeta()
  const tickers = companies.map((c) => c.ticker)
  const groups = new Map<string, Stat[]>()
  stats.filter((s) => s.kind !== 'value' && s.value != null).forEach((s) => {
    const k = s.series ?? ''
    groups.set(k, [...(groups.get(k) ?? []), s])
  })
  if (!groups.size) return null
  return (
    <div className="stat-grid">
      {[...groups.entries()].map(([series, ss], i) => {
        const main = ss.find((s) => s.kind === 'change' || s.kind === 'change_pp') ?? ss[0]
        const cagr = ss.find((s) => s.kind === 'cagr' && s !== main)
        const pp = main.kind === 'change_pp'
        const v = main.value as number
        const good = v >= 0 === goodWhenUp
        const isCo = tickers.includes(series)
        return (
          <Rise key={series + i} i={i} className="stat-tile">
            <Statistic
              title={
                <>
                  <span className="swatch" style={{ background: seriesColor(t, isCo ? series : null, i) }} />
                  <span style={{ color: 'var(--ink-2)', fontWeight: 500 }}>{isCo ? series : metricLabel(series)}</span>
                  <span className="mono" style={{ fontSize: 10.5, marginLeft: 'auto' }}>{main.period_from} → {main.period_to}</span>
                </>
              }
              value={v}
              formatter={() => (
                <span className="num">
                  {v >= 0 ? '+' : '−'}
                  <CountUp value={Math.abs(v)} format={(x) => x.toFixed(pp ? 2 : 1)} />
                </span>
              )}
              suffix={<span style={{ fontSize: 13, color: 'var(--muted)' }}>{pp ? 'pp' : '%'} {main.kind === 'cagr' ? 'a year' : 'total'}</span>}
              styles={{ content: { fontWeight: 600, letterSpacing: '-0.02em' } }}
            />
            <div className="stat-foot">
              {cagr && cagr.value != null && <span><b className="num" style={{ color: 'var(--ink)', fontWeight: 500 }}>{cagr.value >= 0 ? '+' : '−'}{Math.abs(cagr.value).toFixed(1)}%</b> a year (CAGR)</span>}
              {Math.abs(v) >= 0.005 && (
                <Tag className={good ? 'tag-up' : 'tag-down'} style={{ marginLeft: 'auto', marginInlineEnd: 0 }}>
                  {good ? 'improved' : 'weakened'}
                </Tag>
              )}
            </div>
          </Rise>
        )
      })}
    </div>
  )
}
