import { Badge, Card, Col, Flex, Row, Statistic, Tag, Typography, theme } from 'antd'
import type { Stat } from '../lib/api'
import { useMeta } from '../lib/meta'
import { seriesColor, useTokens } from '../lib/theme'

/** Headline changes of a trend or comparison: one card per series (company or metric) with its
 *  total change and, for amounts, the CAGR. */
export function StatStrip({ stats, goodWhenUp }: { stats: Stat[]; goodWhenUp: boolean }) {
  const t = useTokens()
  const { token } = theme.useToken()
  const { companies, metricLabel } = useMeta()
  const tickers = companies.map((c) => c.ticker)
  const groups = new Map<string, Stat[]>()
  stats.filter((s) => s.kind !== 'value' && s.value != null).forEach((s) => {
    const k = s.series ?? ''
    groups.set(k, [...(groups.get(k) ?? []), s])
  })
  if (!groups.size) return null
  const shown = [...groups.keys()].filter((k) => tickers.includes(k))
  return (
    <Row gutter={[16, 16]}>
      {[...groups.entries()].map(([series, ss], i) => {
        const main = ss.find((s) => s.kind === 'change' || s.kind === 'change_pp') ?? ss[0]
        const cagr = ss.find((s) => s.kind === 'cagr' && s !== main)
        const pp = main.kind === 'change_pp'
        const v = main.value as number
        const flat = Math.abs(v) < 0.005
        const good = v >= 0 === goodWhenUp
        const isCo = tickers.includes(series)
        return (
          <Col key={series + i} xs={24} sm={12} xl={8}>
            <Card size="small">
              <Statistic
                title={
                  <Flex justify="space-between" gap="small">
                    <Badge color={seriesColor(t, isCo ? series : null, i, shown)} text={isCo ? series : metricLabel(series)} />
                    <Typography.Text type="secondary">{main.period_from} → {main.period_to}</Typography.Text>
                  </Flex>
                }
                value={Math.abs(v)}
                precision={pp ? 2 : 1}
                prefix={v >= 0 ? '+' : '−'}
                suffix={pp ? 'pp' : '%'}
                styles={{ content: { color: flat ? undefined : good ? token.colorSuccess : token.colorError } }}
              />
              <Flex justify="space-between" align="center" style={{ marginTop: token.marginXS }}>
                <Typography.Text type="secondary">
                  {cagr && cagr.value != null ? `${cagr.value >= 0 ? '+' : '−'}${Math.abs(cagr.value).toFixed(1)}% a year (CAGR)` : main.kind === 'cagr' ? 'a year' : 'total change'}
                </Typography.Text>
                {!flat && <Tag color={good ? 'success' : 'error'} style={{ margin: 0 }}>{good ? 'Improved' : 'Weakened'}</Tag>}
              </Flex>
            </Card>
          </Col>
        )
      })}
    </Row>
  )
}
