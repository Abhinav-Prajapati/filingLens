import { useMemo, useState } from 'react'
import type { EChartsOption } from 'echarts'
import { Card, Flex, Grid, Segmented, Typography, theme } from 'antd'
import {
  BarChartOutlined, BarsOutlined, FundOutlined, HeatMapOutlined, LineChartOutlined, NodeIndexOutlined, PieChartOutlined, RiseOutlined, StockOutlined,
} from '@ant-design/icons'
import type { DataView } from '../lib/api'
import { fmtChange, fmtCompact, isIndexUnit, isPctUnit, unitLabel } from '../lib/format'
import { useMeta } from '../lib/meta'
import { seriesColor, useTokens, type Tokens } from '../lib/theme'
import { Legend } from '../components/bits'
import { EChart } from './EChart'
import {
  barOption, changeOption, donutOption, heatmapOption, lineOption, rankedOption, slopeOption, waterfallOption, type S, type Step,
} from './options'

export type ViewId = 'bar' | 'line' | 'yoy' | 'bridge' | 'grouped' | 'indexed' | 'slope' | 'heatmap' | 'ranked' | 'donut'

const VIEW_META: Record<ViewId, { label: string; icon: React.ReactNode }> = {
  bar: { label: 'Bars', icon: <BarChartOutlined /> },
  grouped: { label: 'Grouped', icon: <BarChartOutlined /> },
  line: { label: 'Line', icon: <LineChartOutlined /> },
  yoy: { label: 'YoY', icon: <RiseOutlined /> },
  bridge: { label: 'Bridge', icon: <StockOutlined /> },
  indexed: { label: 'Indexed', icon: <FundOutlined /> },
  slope: { label: 'Slope', icon: <NodeIndexOutlined /> },
  heatmap: { label: 'Heatmap', icon: <HeatMapOutlined /> },
  ranked: { label: 'Ranked', icon: <BarsOutlined /> },
  donut: { label: 'Share', icon: <PieChartOutlined /> },
}

/** Plotted series of a data view, each with its fixed entity colour. */
export function seriesOf(d: DataView, t: Tokens, label: (k: string) => string): S[] {
  if (!d.chart) return []
  const cos = d.chart.series.flatMap((s) => (s.ticker && (s.key === 'value' || s.key === s.ticker) ? [s.ticker] : []))
  return d.chart.series.map((s, i) => ({
    key: s.key,
    label: s.key === 'value' ? (s.ticker ?? s.label) : s.ticker && s.key === s.ticker ? s.ticker : label(s.key) || s.label,
    color: seriesColor(t, s.key === 'value' || s.key === s.ticker ? s.ticker : null, i, cos),
    points: s.points,
  }))
}

interface Plan { views: ViewId[]; initial: ViewId }

function plan(d: DataView, series: S[]): Plan {
  const unit = d.unit
  const periods = d.table?.rows.map((r) => String(r.period)) ?? []
  const pct = isPctUnit(unit)
  const normalised = isIndexUnit(unit) || unit === '% of total'
  if (d.tool === 'get_trend') {
    const vals = series[0]?.points.map((p) => p.value) ?? []
    const nonNull = vals.filter((v) => v != null).length
    const changeCol = d.table?.columns.find((c) => c.type === 'change')
    const changes = changeCol ? d.table!.rows.filter((r) => r[changeCol.key] != null).length : 0
    const quarterly = periods.some((p) => p.includes('Q'))
    const views: ViewId[] = ['line', 'bar']
    if (changes >= 2) views.push('yoy')
    if (!pct && !quarterly && nonNull >= 3 && vals.every((v) => v != null)) views.push('bridge')
    return { views, initial: d.chart?.type === 'bar' ? 'bar' : 'line' }
  }
  // compare
  if (periods.length === 1) {
    const allPositive = series.every((s) => (s.points[0]?.value ?? 0) > 0)
    const views: ViewId[] = ['ranked']
    if (unit === '% of total' || (!pct && !normalised && allPositive && series.length >= 2)) views.push('donut')
    views.push('grouped')
    return { views, initial: unit === '% of total' ? 'donut' : 'ranked' }
  }
  const views: ViewId[] = ['grouped', 'line']
  if (!pct && !normalised && series.length >= 2) views.push('indexed')
  if (series.length >= 2) views.push('slope')
  if (series.length >= 2) views.push('heatmap')
  return { views, initial: d.chart?.type === 'line' || periods.length > 6 ? 'line' : 'grouped' }
}

function indexed(series: S[]): S[] {
  return series.map((s) => {
    const first = s.points.find((p) => p.value != null)?.value
    return { ...s, points: s.points.map((p) => ({ period: p.period, value: p.value != null && first ? (p.value / first) * 100 : null })) }
  })
}

function build(view: ViewId, d: DataView, series: S[], t: Tokens): { option: EChartsOption; caption: string; legend: boolean; height?: number } {
  const unit = d.unit
  const pct = isPctUnit(unit)
  const periods = series[0]?.points.map((p) => p.period) ?? []
  switch (view) {
    case 'bar':
    case 'grouped':
      return { option: barOption(t, series, unit), caption: `${unitLabel(unit)}`, legend: true }
    case 'line':
      return {
        option: lineOption(t, series, unit),
        caption: periods.some((_, i) => series.some((s) => s.points[i]?.value == null)) ? `${unitLabel(unit)} · gaps are periods with no filed figure` : unitLabel(unit),
        legend: true,
      }
    case 'indexed':
      return { option: lineOption(t, indexed(series), 'index', { area: false }), caption: `Each series rebased to its first period = 100, so growth rates compare directly`, legend: true }
    case 'yoy': {
      const col = d.table!.columns.find((c) => c.type === 'change')!
      const u = col.unit === 'pp' ? 'pp' : '%'
      const name = u === 'pp' ? 'Change vs previous year' : 'Year-on-year growth'
      return {
        option: changeOption(t, d.table!.rows.map((r) => String(r.period)), d.table!.rows.map((r) => (r[col.key] as number | null) ?? null), u, name),
        caption: u === 'pp' ? 'Change in percentage points vs the previous year' : 'Growth vs the same period a year earlier', legend: false,
      }
    }
    case 'bridge': {
      const s = series[0]
      const pts = s.points as { period: string; value: number }[]
      const steps: Step[] = [{ label: pts[0].period, value: pts[0].value, kind: 'total' }]
      for (let i = 1; i < pts.length; i++) steps.push({ label: `Δ ${pts[i].period}`, value: pts[i].value - pts[i - 1].value, kind: 'delta' })
      steps.push({ label: pts[pts.length - 1].period, value: pts[pts.length - 1].value, kind: 'total' })
      return { option: waterfallOption(t, steps, unit, s.color), caption: `How ${pts[0].period} became ${pts[pts.length - 1].period}: each year's change, ${unitLabel(unit)}`, legend: false }
    }
    case 'slope':
      return { option: slopeOption(t, series, periods[0], periods[periods.length - 1], unit), caption: `${periods[0]} vs ${periods[periods.length - 1]}; change shown at the right`, legend: true }
    case 'heatmap': {
      if (pct) {
        const cells: [number, number, number | null][] = []
        series.forEach((s, y) => s.points.forEach((p, x) => cells.push([x, y, p.value])))
        return {
          option: heatmapOption(t, periods, series.map((s) => s.label), cells, { diverging: false, unit, fmt: (v) => `${v.toFixed(1)}%` }),
          caption: 'Darker = higher', legend: false, height: Math.max(200, series.length * 56 + 90),
        }
      }
      const xs = periods.slice(1).map((p, i) => `${periods[i]}→${p}`)
      const cells: [number, number, number | null][] = []
      series.forEach((s, y) => xs.forEach((_, x) => {
        const a = s.points[x]?.value, b = s.points[x + 1]?.value
        cells.push([x, y, a != null && b != null && a !== 0 ? ((b - a) / Math.abs(a)) * 100 : null])
      }))
      return {
        option: heatmapOption(t, xs, series.map((s) => s.label), cells, { diverging: true, unit: '%', fmt: (v) => fmtChange(v) }),
        caption: 'Growth between the periods shown; blue = growth, red = decline', legend: false, height: Math.max(200, series.length * 56 + 90),
      }
    }
    case 'ranked':
      return {
        option: rankedOption(t, series.map((s) => ({ label: s.label, value: s.points[0]?.value ?? null, color: s.color })), unit),
        caption: `${periods[0] ?? ''} · ${unitLabel(unit)}`, legend: false, height: Math.max(150, series.length * 46 + 30),
      }
    case 'donut': {
      const items = series.map((s) => ({ label: s.label, value: s.points[0]?.value ?? 0, color: s.color }))
      const total = items.reduce((a, i) => a + i.value, 0)
      return {
        option: donutOption(t, items, unit === '% of total'
          ? { value: periods[0] ?? '', label: 'Share of total' }
          : { value: fmtCompact(total, unit), label: `Combined ${periods[0] ?? ''}` }),
        caption: 'Each company’s share of the combined total', legend: true, height: 280,
      }
    }
  }
}

export function ChartPanel({ data, initial, title, bare }: { data: DataView; initial?: ViewId; title?: string; bare?: boolean }) {
  const t = useTokens()
  const { token } = theme.useToken()
  const wide = Grid.useBreakpoint().md
  const { metricLabel } = useMeta()
  const series = useMemo(() => seriesOf(data, t, metricLabel), [data, t, metricLabel])
  const p = useMemo(() => plan(data, series), [data, series])
  const [view, setView] = useState<ViewId>(initial ?? p.initial)
  const active = p.views.includes(view) ? view : p.initial
  const built = useMemo(() => build(active, data, series, t), [active, data, series, t])
  const y = title ?? data.chart?.y_label ?? ''
  if (!series.length) return null
  const switcher = p.views.length > 1 && (
    <Segmented<ViewId>
      size="small"
      value={active}
      onChange={setView}
      options={p.views.map((v) => ({ value: v, label: VIEW_META[v].label, icon: VIEW_META[v].icon }))}
      aria-label="Chart type"
    />
  )
  const body = (
    <Flex vertical gap="small">
      {!wide && switcher && <div style={{ overflowX: 'auto' }}>{switcher}</div>}
      {built.legend && <Legend items={series.map((s) => ({ label: s.label, color: s.color }))} />}
      <EChart option={built.option} height={built.height} label={`${y}: ${VIEW_META[active].label} chart. The table below has every value.`} />
      <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM }}>{built.caption}</Typography.Text>
    </Flex>
  )
  if (bare) {
    return <Flex vertical gap="small">{wide && switcher && <Flex justify="flex-end">{switcher}</Flex>}{body}</Flex>
  }
  return (
    <Card
      size="small"
      title={<Flex vertical><Typography.Text strong>{y}</Typography.Text><Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM, fontWeight: 400 }}>{unitLabel(data.unit)}</Typography.Text></Flex>}
      extra={wide ? switcher : undefined}
      styles={{ header: { paddingBlock: token.paddingXS } }}
    >
      {body}
    </Card>
  )
}
