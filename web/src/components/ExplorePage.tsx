import { useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  Alert, Badge, Card, Col, Descriptions, Divider, Flex, Row, Segmented, Select, Skeleton, Space, Statistic, Tag, Tooltip, Typography,
  theme,
} from 'antd'
import { api, type DataView } from '../lib/api'
import { fmtChange, fmtCompact, fmtValue } from '../lib/format'
import { useMeta } from '../lib/meta'
import { seriesColor, useTokens } from '../lib/theme'
import { ChartPanel } from '../charts/ChartPanel'
import { EChart } from '../charts/EChart'
import { donutOption, heatmapOption, radarOption, sparkOption, waterfallOption, type Step } from '../charts/options'
import { PageHeader } from './Chrome'
import { Delta, Legend } from './bits'

const RATIOS = ['net_margin', 'pbt_margin', 'effective_tax_rate', 'employee_cost_ratio']
const GROWTH = ['revenue_from_operations', 'total_income', 'profit_before_tax', 'net_profit', 'eps_basic', 'employee_expense', 'total_assets', 'total_equity']
const BRIDGE = ['revenue_from_operations', 'other_income', 'employee_expense', 'depreciation_amortisation', 'finance_costs', 'total_expenses', 'profit_before_tax', 'tax_expense', 'net_profit']
const SIZE_METRICS = ['revenue_from_operations', 'net_profit', 'profit_before_tax', 'total_assets', 'total_equity', 'employee_expense']

// a chart tells at most 8 companies apart (the preset palette); the compare API takes 8 too
const MAX_PICK = 8
const DEFAULT_PICK = 5

const fyNum = (fy: string) => parseInt(fy.replace(/\D/g, ''), 10)
const prevFy = (fy: string) => `FY${String(fyNum(fy) - 1).padStart(2, '0')}`

/** Run an async loader whenever its key changes; keeps the last good value while reloading. */
function useLoad<T>(key: string, load: () => Promise<T>): { data: T | null; error: string | null; loading: boolean } {
  const [state, setState] = useState<{ key: string; data: T | null; error: string | null }>({ key: '', data: null, error: null })
  useEffect(() => {
    let live = true
    load().then((data) => live && setState({ key, data, error: null }))
      .catch((e: Error) => live && setState({ key, data: null, error: e.message }))
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return { data: state.data, error: state.error, loading: state.key !== key }
}

export function ExplorePage() {
  const { loaded, metricLabel, metrics } = useMeta()
  const allFys = useMemo(() => [...new Set(loaded.flatMap((c) => c.fys))].sort((a, b) => fyNum(a) - fyNum(b)), [loaded])
  const [picked, setPicked] = useState<string[]>(() => loaded.slice(0, DEFAULT_PICK).map((c) => c.ticker))
  const [fy, setFy] = useState(() => allFys[allFys.length - 1] ?? 'FY26')
  const [sizeMetric, setSizeMetric] = useState('revenue_from_operations')
  const [ratio, setRatio] = useState('net_margin')
  const [bridgeCo, setBridgeCo] = useState(() => loaded[0]?.ticker ?? '')
  const tickers = loaded.map((c) => c.ticker).filter((t) => picked.includes(t))
  const ratioOptions = metrics.filter((m) => m.kind === 'ratio')

  const toggle = (t: string) => setPicked((p) => (p.includes(t) ? (p.length > 1 ? p.filter((x) => x !== t) : p) : p.length < MAX_PICK ? [...p, t] : p))
  const bridgeTicker = tickers.includes(bridgeCo) ? bridgeCo : tickers[0]

  return (
    <div style={{ maxWidth: 1400, margin: '0 auto' }}>
      <PageHeader title="Explore" description="Size, growth, margins and cost structure across the companies you cover" />
      <Flex vertical gap="middle">
        <Card size="small">
          <Flex justify="space-between" align="center" gap="middle" wrap role="toolbar" aria-label="Filters">
            <Flex align="center" gap="small" wrap>
              <Typography.Text type="secondary">Companies</Typography.Text>
              {loaded.map((c) => (
                <CompanyToggle
                  key={c.ticker} ticker={c.ticker} name={c.name} picked={tickers}
                  full={!picked.includes(c.ticker) && tickers.length >= MAX_PICK} onClick={() => toggle(c.ticker)}
                />
              ))}
              {loaded.length > MAX_PICK && <Typography.Text type="secondary">{tickers.length} of {MAX_PICK} max</Typography.Text>}
            </Flex>
            <Flex align="center" gap="small">
              <Typography.Text type="secondary">Fiscal year</Typography.Text>
              <Segmented value={fy} onChange={setFy} options={allFys.slice(1)} aria-label="Fiscal year" />
            </Flex>
          </Flex>
        </Card>

        <Row gutter={[16, 16]}>
          {tickers.map((t) => <Col key={t} xs={24} sm={12} xl={8} xxl={6}><Kpi ticker={t} fy={fy} set={tickers} /></Col>)}
        </Row>

        <Row gutter={[16, 16]}>
          <Col xs={24} xl={14}><IndexedGrowth tickers={tickers} metric={sizeMetric} setMetric={setSizeMetric} /></Col>
          <Col xs={24} xl={10}><RatioRadar tickers={tickers} fy={fy} /></Col>
          <Col xs={24} xl={16}><ProfitBridge ticker={bridgeTicker} tickers={tickers} setTicker={setBridgeCo} fy={fy} /></Col>
          <Col xs={24} xl={8}><ShareDonut tickers={tickers} fy={fy} metric={sizeMetric} /></Col>
          <Col xs={24} xl={14}>
            <RatioOverTime tickers={tickers} ratio={ratio} setRatio={setRatio} options={ratioOptions.map((m) => ({ id: m.id, label: m.label }))} />
          </Col>
          <Col xs={24} xl={10}><GrowthHeatmap tickers={tickers} fy={fy} label={metricLabel} /></Col>
        </Row>
      </Flex>
    </div>
  )
}

function CompanyToggle({ ticker, name, picked, full, onClick }: { ticker: string; name: string; picked: string[]; full: boolean; onClick: () => void }) {
  const t = useTokens()
  const on = picked.includes(ticker)
  return (
    <Tooltip title={full ? `${name}. Up to ${MAX_PICK} companies at once: deselect one first` : name}>
      <Tag.CheckableTag checked={on} onChange={onClick} disabled={full}>
        <Space size={4}><Badge color={on ? seriesColor(t, ticker, 0, picked) : t.muted} />{ticker}</Space>
      </Tag.CheckableTag>
    </Tooltip>
  )
}

/** A dashboard panel: antd Card with title, optional subtitle and a control on the right. */
function Panel({ title, sub, extra, children }: { title: string; sub?: string; extra?: ReactNode; children: ReactNode }) {
  const { token } = theme.useToken()
  return (
    <Card
      style={{ height: '100%' }}
      title={
        <Flex vertical>
          <Typography.Text strong>{title}</Typography.Text>
          {sub && <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM, fontWeight: 400 }}>{sub}</Typography.Text>}
        </Flex>
      }
      extra={extra}
    >
      <Flex vertical gap="small">{children}</Flex>
    </Card>
  )
}

function Caption({ children }: { children: ReactNode }) {
  const { token } = theme.useToken()
  return <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM }}>{children}</Typography.Text>
}

const ErrorNote = ({ msg }: { msg: string }) => <Alert type="error" showIcon title={msg} />
const Loading = () => <Skeleton active paragraph={{ rows: 6 }} title={false} />

// ---- KPI card -----------------------------------------------------------------------------
function Kpi({ ticker, fy, set }: { ticker: string; fy: string; set: string[] }) {
  const t = useTokens()
  const { companyName } = useMeta()
  const { data } = useLoad(`kpi-${ticker}`, () => Promise.all([
    api.trend({ ticker, metric: 'revenue_from_operations' }),
    api.trend({ ticker, metric: 'net_profit' }),
    api.trend({ ticker, metric: 'net_margin' }),
    api.trend({ ticker, metric: 'eps_basic' }),
  ]))
  const color = seriesColor(t, ticker, 0, set)
  const at = (d: DataView | undefined, p: string) => (d?.table?.rows.find((r) => r.period === p)?.value as number | null) ?? null
  const [rev, np, nm, eps] = data ?? []
  const r = at(rev, fy), rp = at(rev, prevFy(fy))
  const growth = r != null && rp ? ((r - rp) / rp) * 100 : null
  const points = rev?.chart?.series[0]?.points ?? []
  const lakh = r != null && r >= 1e5
  const nmNow = at(nm, fy), nmPrev = at(nm, prevFy(fy))
  return (
    <Card
      size="small"
      style={{ height: '100%' }}
      title={
        <Flex align="center" gap="small" style={{ minWidth: 0 }}>
          <Badge color={color} />
          <Typography.Text strong ellipsis={{ tooltip: companyName(ticker) }} style={{ minWidth: 0 }}>{companyName(ticker)}</Typography.Text>
        </Flex>
      }
      extra={<Tag style={{ margin: 0 }}>{ticker}</Tag>}
    >
      {!data ? <Skeleton active paragraph={{ rows: 4 }} title={false} /> : (
        <Flex vertical gap="small">
          <Flex justify="space-between" align="flex-end">
            <Statistic
              title={`Revenue · ${fy}`}
              value={r == null ? '–' : lakh ? r / 1e5 : r}
              precision={lakh ? 2 : 0}
              prefix={r == null ? undefined : '₹'}
              suffix={r == null ? undefined : lakh ? 'L Cr' : 'Cr'}
            />
            <Delta value={growth} />
          </Flex>
          {points.length > 1 && <EChart height={64} option={sparkOption(t, points, color, 'INR crore', fy)} label={`${ticker} revenue history`} />}
          <Divider style={{ margin: 0 }} />
          <Descriptions
            size="small"
            column={2}
            colon={false}
            items={[
              { key: 'np', label: 'Net profit', children: fmtCompact(at(np, fy), 'INR crore') },
              { key: 'nm', label: 'Net margin', children: fmtValue(nmNow, '%') },
              { key: 'eps', label: 'EPS', children: fmtValue(at(eps, fy), 'INR') },
              { key: 'nmd', label: 'Margin Δ', children: nmNow != null && nmPrev != null ? fmtChange(nmNow - nmPrev, 'pp') : '–' },
            ]}
          />
        </Flex>
      )}
    </Card>
  )
}

// ---- indexed growth line ----------------------------------------------------------------------
function IndexedGrowth({ tickers, metric, setMetric }: { tickers: string[]; metric: string; setMetric: (m: string) => void }) {
  const { metricLabel } = useMeta()
  const { data, error } = useLoad(`idx-${tickers.join()}-${metric}`, () => api.compare({ tickers, metrics: [metric], normalise: 'index100' }))
  return (
    <Panel
      title="Growth since the first year" sub="indexed, first year = 100"
      extra={<Select size="small" value={metric} onChange={setMetric} aria-label="Metric" style={{ width: 210 }} options={SIZE_METRICS.map((m) => ({ value: m, label: metricLabel(m) }))} />}
    >
      {error ? <ErrorNote msg={error} /> : data ? <ChartPanelBody data={data} /> : <Loading />}
    </Panel>
  )
}

/** ChartPanel without its own frame (the dashboard panel already is one); keeps the view switcher. */
function ChartPanelBody({ data }: { data: DataView }) {
  return <ChartPanel key={data.title + data.unit + (data.table?.rows.length ?? 0)} data={data} initial="line" bare />
}

// ---- ratio radar --------------------------------------------------------------------------------
function RatioRadar({ tickers, fy }: { tickers: string[]; fy: string }) {
  const t = useTokens()
  const { metricLabel } = useMeta()
  const { data, error } = useLoad(`radar-${tickers.join()}-${fy}`, () =>
    Promise.all(RATIOS.map((m) => api.compare({ tickers, metrics: [m], fys: [fy] }))))
  const option = useMemo(() => {
    if (!data) return null
    const row = (d: DataView) => d.table?.rows[0] ?? {}
    const short: Record<string, string> = { net_margin: 'Net margin', pbt_margin: 'PBT margin', effective_tax_rate: 'Tax rate', employee_cost_ratio: 'Staff cost / revenue' }
    const axes = RATIOS.map((m, i) => {
      const vals = tickers.map((tk) => (row(data[i])[tk] as number | null)).filter((v): v is number => v != null)
      const hi = Math.max(...vals, 1), lo = Math.min(...vals, hi)
      return { name: short[m] ?? metricLabel(m), max: Math.ceil((hi + (hi - lo) * 0.25 + 1) / 5) * 5, min: Math.max(0, Math.floor((lo - (hi - lo) * 0.6 - 2) / 5) * 5) }
    })
    const series = tickers.map((tk) => ({ label: tk, color: seriesColor(t, tk, 0, tickers), values: RATIOS.map((_, i) => (row(data[i])[tk] as number | null) ?? null) }))
    return radarOption(t, axes, series, '%')
  }, [data, tickers, t, metricLabel])
  return (
    <Panel title="Ratio profile" sub={`${fy} · % of revenue / PBT`}>
      <Legend items={tickers.map((tk) => ({ label: tk, color: seriesColor(t, tk, 0, tickers) }))} />
      {error ? <ErrorNote msg={error} /> : option ? <EChart option={option} height={300} label={`Four ratios for ${tickers.join(', ')} in ${fy}`} /> : <Loading />}
      <Caption>Each axis has its own range (not from zero), so gaps are exaggerated; hover for exact values</Caption>
    </Panel>
  )
}

// ---- revenue -> net profit bridge -----------------------------------------------------------------
function ProfitBridge({ ticker, tickers, setTicker, fy }: { ticker: string; tickers: string[]; setTicker: (t: string) => void; fy: string }) {
  const t = useTokens()
  // the API compares at most 5 metrics per call: fetch in two and merge the rows
  const { data, error } = useLoad(`bridge-${ticker}-${fy}`, () => Promise.all([
    api.compare({ tickers: [ticker], metrics: BRIDGE.slice(0, 5), fys: [fy] }),
    api.compare({ tickers: [ticker], metrics: BRIDGE.slice(5), fys: [fy] }),
  ]))
  const steps = useMemo<Step[] | null>(() => {
    const r = data ? { ...data[0].table?.rows[0], ...data[1].table?.rows[0] } : null
    if (!r) return null
    const g = (k: string) => (r[k] as number | null) ?? null
    const [rev, oi, emp, dna, fin, tot, pbt, tax, np] = BRIDGE.map(g)
    if (rev == null || tot == null || pbt == null || np == null) return null
    const known = (emp ?? 0) + (dna ?? 0) + (fin ?? 0)
    const s: Step[] = [{ label: 'Revenue', value: rev, kind: 'total' }]
    if (oi) s.push({ label: 'Other income', value: oi, kind: 'delta' })
    if (emp) s.push({ label: 'Staff cost', value: -emp, kind: 'delta' })
    s.push({ label: 'Other costs', value: -(tot - known), kind: 'delta' })
    if (dna) s.push({ label: 'D&A', value: -dna, kind: 'delta' })
    if (fin) s.push({ label: 'Finance', value: -fin, kind: 'delta' })
    const gap = pbt - (rev + (oi ?? 0) - tot)
    if (Math.abs(gap) >= 1) s.push({ label: 'One-offs', value: gap, kind: 'delta' })
    s.push({ label: 'PBT', value: pbt, kind: 'total' })
    if (tax) s.push({ label: 'Tax', value: -tax, kind: 'delta' })
    const gap2 = np - (pbt - (tax ?? 0))
    if (Math.abs(gap2) >= 1) s.push({ label: 'Other items', value: gap2, kind: 'delta' })
    s.push({ label: 'Net profit', value: np, kind: 'total' })
    return s
  }, [data])
  return (
    <Panel
      title="From revenue to net profit" sub={`${fy} · ₹ crore`}
      extra={<Select size="small" value={ticker} onChange={setTicker} options={tickers.map((tk) => ({ value: tk, label: tk }))} style={{ width: 160 }} aria-label="Company" />}
    >
      {error ? <ErrorNote msg={error} />
        : steps ? <EChart option={waterfallOption(t, steps, 'INR crore', seriesColor(t, ticker, 0, tickers))} height={320} label={`${ticker} ${fy} profit bridge`} />
          : data ? <Alert type="info" showIcon title={`Not every line item is filed for ${ticker} ${fy}, so the bridge is not drawn.`} /> : <Loading />}
      <Caption>Blue adds, red subtracts. “One-offs” and “Other items” are gaps between filed totals (exceptional items, discontinued operations), shown rather than hidden.</Caption>
    </Panel>
  )
}

// ---- share donut ----------------------------------------------------------------------------------
function ShareDonut({ tickers, fy, metric }: { tickers: string[]; fy: string; metric: string }) {
  const t = useTokens()
  const { metricLabel } = useMeta()
  const { data, error } = useLoad(`share-${tickers.join()}-${fy}-${metric}`, () => api.compare({ tickers, metrics: [metric], fys: [fy] }))
  const row = data?.table?.rows[0]
  const items = row ? tickers.map((tk) => ({ label: tk, value: (row[tk] as number | null) ?? 0, color: seriesColor(t, tk, 0, tickers) })).filter((i) => i.value > 0) : []
  const total = items.reduce((a, i) => a + i.value, 0)
  return (
    <Panel title="Share of the group" sub={`${metricLabel(metric)} · ${fy}`}>
      <Legend items={items.map((i) => ({ label: `${i.label} ${total ? ((i.value / total) * 100).toFixed(1) : 0}%`, color: i.color }))} />
      {error ? <ErrorNote msg={error} />
        : items.length > 0 ? <EChart option={donutOption(t, items, { value: fmtCompact(total, 'INR crore'), label: 'Combined' })} height={290} label={`Share of combined ${metricLabel(metric)}`} /> : <Loading />}
    </Panel>
  )
}

// ---- ratio over time (reuses the answer chart panel) ------------------------------------------------
function RatioOverTime({ tickers, ratio, setRatio, options }: { tickers: string[]; ratio: string; setRatio: (r: string) => void; options: { id: string; label: string }[] }) {
  const { data, error } = useLoad(`ratio-${tickers.join()}-${ratio}`, () => api.compare({ tickers, metrics: [ratio] }))
  return (
    <Panel
      title="Ratios over time" sub="each company, every filed year"
      extra={<Select size="small" value={ratio} onChange={setRatio} aria-label="Ratio" style={{ width: 200 }} options={options.map((o) => ({ value: o.id, label: o.label }))} />}
    >
      {error ? <ErrorNote msg={error} /> : data ? <ChartPanelBody data={data} /> : <Loading />}
    </Panel>
  )
}

// ---- growth heatmap ---------------------------------------------------------------------------------
function GrowthHeatmap({ tickers, fy, label }: { tickers: string[]; fy: string; label: (m: string) => string }) {
  const t = useTokens()
  const { data, error } = useLoad(`heat-${tickers.join()}-${fy}`, () =>
    Promise.all(GROWTH.map((m) => api.compare({ tickers, metrics: [m], fys: [prevFy(fy), fy] }))))
  const option = useMemo(() => {
    if (!data) return null
    const cells: [number, number, number | null][] = []
    GROWTH.forEach((_, y) => {
      const rows = data[y].table?.rows ?? []
      const a = rows.find((r) => r.period === prevFy(fy)), b = rows.find((r) => r.period === fy)
      tickers.forEach((tk, x) => {
        const va = a?.[tk] as number | null | undefined, vb = b?.[tk] as number | null | undefined
        cells.push([x, y, va != null && vb != null && va !== 0 ? ((vb - va) / Math.abs(va)) * 100 : null])
      })
    })
    return heatmapOption(t, tickers, GROWTH.map((m) => label(m).replace(' (profit for the period)', '').replace('Basic earnings per share', 'EPS (basic)')), cells,
      { diverging: true, unit: '%', fmt: (v) => fmtChange(v) })
  }, [data, tickers, fy, t, label])
  return (
    <Panel title="Year-on-year growth" sub={`${prevFy(fy)} → ${fy}`}>
      {error ? <ErrorNote msg={error} /> : option ? <EChart option={option} height={GROWTH.length * 40 + 70} label={`Growth ${prevFy(fy)} to ${fy} by metric and company`} /> : <Loading />}
      <Caption>Blue = growth, red = decline; colour scale is symmetric around zero</Caption>
    </Panel>
  )
}
