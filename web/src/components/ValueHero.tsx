import { useEffect, useState } from 'react'
import { Card, Col, Flex, Row, Statistic, Tag, Typography, theme } from 'antd'
import { TrophyOutlined } from '@ant-design/icons'
import { api, type DataView, type Stat } from '../lib/api'
import { isPctUnit, isPerShare, unitLabel } from '../lib/format'
import { useMeta } from '../lib/meta'
import { seriesColor, useTokens } from '../lib/theme'
import { EChart } from '../charts/EChart'
import { rankedOption, sparkOption } from '../charts/options'
import { Delta } from './bits'

interface Args { ticker?: string; metric?: string; fy?: string; quarter?: string | null }

/** One figure, with context fetched around it: its own history and where peers stand. */
export function ValueHero({ stat, args, goodWhenUp }: { stat: Stat; args: Args; goodWhenUp: boolean }) {
  const t = useTokens()
  const { token } = theme.useToken()
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
    ? loaded.map((c) => ({ label: c.ticker, value: (peerRow[c.ticker] as number | null) ?? null, color: c.ticker === ticker ? color : t.muted, highlight: c.ticker === ticker }))
        .filter((p) => p.value != null)
    : []
  const rank = peerItems.length > 1 ? [...peerItems].sort((a, b) => (b.value as number) - (a.value as number)).findIndex((p) => p.highlight) + 1 : 0

  const v = stat.value
  const pct = isPctUnit(unit)
  const per = isPerShare(unit)

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={10}>
        <Card style={{ height: '100%' }}>
          <Statistic
            title={`${companyName(ticker)} · ${metricLabel(metric)} · ${period}`}
            value={v ?? '–'}
            precision={pct || per ? 2 : 0}
            prefix={!pct && v != null ? '₹' : undefined}
            suffix={pct ? '%' : per ? 'per share' : 'crore'}
            groupSeparator=","
          />
          <Typography.Text type="secondary">
            {pct ? (metric.includes('margin') || metric.includes('ratio') ? 'of revenue' : '') : unitLabel(unit)}
            {!pct && !per && v != null && v >= 1e5 ? ` · ₹${(v / 1e5).toFixed(2)} lakh crore` : ''}
          </Typography.Text>
          <Flex gap="small" wrap align="center" style={{ marginTop: token.marginMD }}>
            {change != null && prevPt && (
              <Typography.Text type="secondary"><Delta value={change} unit={pct ? 'pp' : '%'} goodWhenUp={goodWhenUp} /> vs {prevPt.period}</Typography.Text>
            )}
            {rank > 0 && <Tag icon={<TrophyOutlined />} style={{ margin: 0 }}>#{rank} of {peerItems.length} covered companies</Tag>}
          </Flex>
        </Card>
      </Col>
      <Col xs={24} lg={14}>
        <Flex vertical gap="middle" style={{ height: '100%' }}>
          <Card size="small" title={`History${quarterly ? ' · quarterly' : ''}`}>
            {pts.length > 1
              ? <EChart size="sm" option={sparkOption(t, pts, color, unit, period)} label={`${ticker} ${metricLabel(metric)} history`} />
              : <Typography.Text type="secondary">No earlier periods filed.</Typography.Text>}
          </Card>
          {peerItems.length > 1 && (
            <Card size="small" title={`Peers · ${fy}`}>
              <EChart option={rankedOption(t, peerItems, unit)} height={peerItems.length * 32 + 16} label={`${metricLabel(metric)} across covered companies, ${fy}`} />
            </Card>
          )}
        </Flex>
      </Col>
    </Row>
  )
}
