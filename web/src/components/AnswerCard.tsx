import { Alert, Card, Divider, Flex, Grid, Space, Tag, Tooltip, Typography, theme } from 'antd'
import {
  BulbOutlined, ClockCircleOutlined, EditOutlined, RobotOutlined, SafetyCertificateOutlined, SearchOutlined, SettingOutlined,
} from '@ant-design/icons'
import type { AnswerView } from '../lib/api'
import { useAuth } from '../lib/auth'
import { useMeta } from '../lib/meta'
import { ChartPanel } from '../charts/ChartPanel'
import { DataTable } from './DataTable'
import { Passages, Sources } from './Sources'
import { StatStrip } from './Stats'
import { ValueHero } from './ValueHero'

const LOWER_IS_BETTER = new Set([
  'employee_expense', 'finance_costs', 'depreciation_amortisation', 'total_expenses', 'tax_expense',
  'effective_tax_rate', 'employee_cost_ratio',
])

const KIND: Record<string, string> = {
  get_value: 'Figure', get_trend: 'Trend', compare: 'Comparison', search_reports: 'From the annual reports', ask_user: 'Needs a closer look',
}

const READ_AS = /^read /                 // notes on how the question was read: shown under the title

type A = Record<string, unknown>

export function AnswerCard({ a }: { a: AnswerView }) {
  const { token } = theme.useToken()
  const wide = Grid.useBreakpoint().md
  const { metricLabel, companyName } = useMeta()
  const d = a.data
  const args = (a.args ?? {}) as A
  const explain = a.mode === 'explain'
  const fargs = (args.figures ?? {}) as A                 // "why": the figures' request
  const metric = (args.metric as string) ?? ((args.metrics as string[]) ?? [])[0]
    ?? (fargs.metric as string) ?? ((fargs.metrics as string[]) ?? [])[0]
  const readAs = a.notes.filter((n) => READ_AS.test(n))
  const goodWhenUp = !LOWER_IS_BETTER.has(metric ?? '')
  const periods = d?.table?.rows.map((r) => String(r.period)) ?? []
  const verified = d?.sources.filter((s) => s.verified === 'pdf').length ?? 0

  let title = d?.title ?? a.question
  if (explain) {
    const tk = (args.tickers as string[]) ?? []
    const names = tk.map((t) => companyName(t).replace(/\s+(limited|ltd\.?)$/i, ''))
    const what = metricLabel(metric).split(' (')[0].toLowerCase()
    title = `Why ${names.join(' & ')}${names.length > 1 ? '' : '’s'} ${what} changed`
  } else if (a.tool === 'get_value' && args.ticker) title = `${companyName(args.ticker as string)} · ${metricLabel(metric)}`
  else if (a.tool === 'get_trend' && args.ticker) title = `${companyName(args.ticker as string)} · ${metricLabel(metric)}`
  else if (a.tool === 'compare') {
    const tk = (d?.chart?.series ?? []).map((s) => s.ticker).filter((x, i, xs) => x && xs.indexOf(x) === i) as string[]
    const ms = (args.metrics as string[]) ?? []
    title = ms.length > 1 ? `${companyName(tk[0])} · ${ms.length} metrics` : `${tk.join(' vs ')} · ${metricLabel(metric)}`
  } else if (a.tool === 'search_reports') title = `“${(args.query as string) ?? a.question}”`
  else if (a.tool === 'ask_user') title = d?.title ? d.title.charAt(0).toUpperCase() + d.title.slice(1) : 'Clarification'
  const span = periods.length > 1 && a.tool !== 'get_value' ? `${periods[0]} – ${periods[periods.length - 1]}` : ''
  const valueStat = d?.stats.find((s) => s.kind === 'value')
  const empty = (msg: string) => <Alert type="info" showIcon title={msg} />

  const kind = explain ? 'Figures and annual report' : KIND[a.tool ?? ''] ?? 'Answer'
  const context = [kind, explain && args.fy ? `${args.fy} report` : span, args.scope && args.scope !== 'consolidated' ? String(args.scope) : '']
    .filter(Boolean).join(' · ')
  const section = (label: string) => <Divider titlePlacement="start" plain style={{ margin: 0 }}>{label}</Divider>
  const hasFigures = (a.tool === 'get_value' && valueStat) || (d && (a.tool === 'get_trend' || a.tool === 'compare')) || (explain && a.figures)

  const isAdmin = useAuth().user?.role === 'admin'
  const tags = (
        <Space size={4} wrap style={{ justifyContent: 'flex-end' }}>
          <Tooltip title="What understood the question">
            <Tag icon={a.mode === 'llm' ? <RobotOutlined /> : a.mode === 'search' ? <SearchOutlined /> : a.mode === 'explain' ? <BulbOutlined /> : <SettingOutlined />} style={{ margin: 0 }}>
              {a.mode === 'llm' ? 'LLM' : a.mode === 'search' ? 'Report search' : a.mode === 'explain' ? 'Figures + reports' : 'Rules'}
            </Tag>
          </Tooltip>
          {verified > 0 && (
            <Tooltip title="Values found on the annual-report page">
              <Tag color="success" icon={<SafetyCertificateOutlined />} style={{ margin: 0 }}>Verified · {verified}</Tag>
            </Tooltip>
          )}
          {(() => {
            const took = (
              <Tag icon={<ClockCircleOutlined />} style={{ margin: 0 }}>
                {a.seconds < 0.001 ? '<1 ms' : a.seconds < 1 ? `${Math.round(a.seconds * 1000)} ms` : `${a.seconds.toFixed(1)} s`}
              </Tag>
            )
            return isAdmin && a.trace_id
              ? <Tooltip title="Open the trace: each step and its time"><a href={`#traces/${a.trace_id}`} aria-label="Open the trace">{took}</a></Tooltip>
              : took
          })()}
        </Space>
  )

  return (
    <Card
      aria-label={`Answer: ${a.question}`}
      styles={{ title: { whiteSpace: 'normal', paddingBlock: token.paddingSM } }}
      title={
        <Flex vertical gap={2}>
          <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM, fontWeight: 400 }}>{context}</Typography.Text>
          <Typography.Text strong style={{ fontSize: token.fontSizeLG }}>{title}</Typography.Text>
          {readAs.length > 0 && (
            <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM, fontWeight: 400 }}>
              <EditOutlined /> {readAs.map((n) => n.charAt(0).toUpperCase() + n.slice(1)).join(' · ')}
            </Typography.Text>
          )}
          {!wide && <div style={{ marginTop: token.marginXS }}>{tags}</div>}
        </Flex>
      }
      extra={wide ? tags : undefined}
    >
      <Flex vertical gap="middle">
        {d?.clarify && <Clarify text={d.clarify} />}
        {a.answer && <Typography.Paragraph style={{ fontSize: token.fontSizeLG, margin: 0 }}>{renderAnswer(a.answer)}</Typography.Paragraph>}
        {a.answer_rejected && (
          <Alert type="warning" showIcon title="Written summary withheld" description={`${a.answer_rejected}. The figures below are from the filings.`} />
        )}

        {hasFigures && (a.answer || d?.clarify || explain) && section('Figures')}
        {a.tool === 'get_value' && valueStat && <ValueHero stat={valueStat} args={args} goodWhenUp={goodWhenUp} />}
        {a.tool === 'get_value' && !valueStat && d?.table === null && !d?.clarify && empty('No filed figure for this period. Nothing is estimated or filled in.')}
        {d && (a.tool === 'get_trend' || a.tool === 'compare') && (
          <>
            {d.stats.length > 0 && <StatStrip stats={d.stats} goodWhenUp={goodWhenUp} />}
            {d.chart && <ChartPanel data={d} />}
            {d.table && <DataTable table={d.table} />}
            {!d.table && empty('No filed figures for this request. Nothing is estimated or filled in.')}
          </>
        )}
        {explain && a.figures && (
          <>
            {a.figures.stats.length > 0 && <StatStrip stats={a.figures.stats} goodWhenUp={goodWhenUp} />}
            {a.figures.chart && <ChartPanel data={a.figures} />}
            {a.figures.table && <DataTable table={a.figures.table} />}
          </>
        )}
        {explain && !a.figures && empty('No filed figures for these years. The report passages are below.')}

        {d && a.tool === 'search_reports' && (
          <>
            {(explain || a.answer) && section('From the annual report')}
            {d.passages.length
              ? <Passages passages={d.passages} query={(args.query as string) ?? a.question} />
              : empty('Nothing relevant found in the loaded annual reports.')}
          </>
        )}
        {d && <Sources sources={[...(a.figures?.sources ?? []), ...d.sources]} notes={[...a.notes.filter((n) => !READ_AS.test(n)), ...d.notes, ...a.errors.map((e) => `model retry: ${e}`)]} />}
      </Flex>
    </Card>
  )
}

/** "… To add: filinglens companies --add X && …" -> the command as code. */
function Clarify({ text }: { text: string }) {
  const [msg, cmd] = text.split(/To add:\s*/)
  return (
    <Alert
      type="info"
      showIcon
      title={msg.trim()}
      description={cmd ? <>To add it, run: <Typography.Text code copyable>{cmd}</Typography.Text></> : undefined}
    />
  )
}

/** Light markdown: **bold** only; the LLM's text is otherwise plain. */
function renderAnswer(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') ? <strong key={i}>{part.slice(2, -2)}</strong> : part)
}
