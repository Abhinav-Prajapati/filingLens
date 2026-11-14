import { Alert, Card, Tag, Tooltip } from 'antd'
import {
  ClockCircleOutlined, RobotOutlined, SafetyCertificateOutlined, SearchOutlined, SettingOutlined,
} from '@ant-design/icons'
import type { AnswerView } from '../lib/api'
import { useMeta } from '../lib/meta'
import { ChartPanel } from '../charts/ChartPanel'
import { DataTable } from './DataTable'
import { Passages, Sources } from './Sources'
import { StatStrip } from './Stats'
import { ValueHero } from './ValueHero'
import { Rise } from './bits'

const LOWER_IS_BETTER = new Set([
  'employee_expense', 'finance_costs', 'depreciation_amortisation', 'total_expenses', 'tax_expense',
  'effective_tax_rate', 'employee_cost_ratio',
])

const KIND: Record<string, string> = {
  get_value: 'Figure', get_trend: 'Trend', compare: 'Comparison', search_reports: 'From the annual reports', ask_user: 'Needs a closer look',
}

type A = Record<string, unknown>

export function AnswerCard({ a }: { a: AnswerView }) {
  const { metricLabel, companyName } = useMeta()
  const d = a.data
  const args = (a.args ?? {}) as A
  const metric = (args.metric as string) ?? ((args.metrics as string[]) ?? [])[0]
  const goodWhenUp = !LOWER_IS_BETTER.has(metric ?? '')
  const periods = d?.table?.rows.map((r) => String(r.period)) ?? []
  const verified = d?.sources.filter((s) => s.verified === 'pdf').length ?? 0

  let title = d?.title ?? a.question
  if (a.tool === 'get_value' && args.ticker) title = `${companyName(args.ticker as string)} · ${metricLabel(metric)}`
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

  return (
    <Card
      className="answer"
      aria-label={`Answer: ${a.question}`}
      styles={{ title: { whiteSpace: 'normal' }, body: { paddingTop: 18 } }}
      title={
        <div>
          <div className="eyebrow">{KIND[a.tool ?? ''] ?? 'Answer'}{span ? ` · ${span}` : ''}{args.scope && args.scope !== 'consolidated' ? ` · ${args.scope}` : ''}</div>
          <h3 className="answer-title">{title}</h3>
        </div>
      }
      extra={
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <Tooltip title="What understood the question">
            <Tag icon={a.mode === 'llm' ? <RobotOutlined /> : a.mode === 'search' ? <SearchOutlined /> : <SettingOutlined />} style={{ margin: 0 }}>
              {a.mode === 'llm' ? 'LLM' : a.mode === 'search' ? 'report search' : 'rules'}
            </Tag>
          </Tooltip>
          {verified > 0 && (
            <Tooltip title="Values found on the annual-report page">
              <Tag className="tag-up" icon={<SafetyCertificateOutlined />} style={{ margin: 0 }}>verified · {verified}</Tag>
            </Tooltip>
          )}
          <Tag icon={<ClockCircleOutlined />} style={{ margin: 0 }} className="mono">
            {a.seconds < 0.001 ? "<1 ms" : a.seconds < 1 ? `${Math.round(a.seconds * 1000)} ms` : `${a.seconds.toFixed(1)} s`}
          </Tag>
        </div>
      }
    >
      <div className="answer-body">
        {d?.clarify && <Clarify text={d.clarify} />}
        {a.answer && <Rise><p className="answer-text">{renderAnswer(a.answer)}</p></Rise>}
        {a.answer_rejected && (
          <Alert type="warning" showIcon title="Written summary withheld" description={`${a.answer_rejected}. The figures below are from the filings.`} />
        )}
        {a.tool === 'get_value' && valueStat && <Rise i={1}><ValueHero stat={valueStat} args={args} goodWhenUp={goodWhenUp} /></Rise>}
        {a.tool === 'get_value' && !valueStat && d?.table === null && !d?.clarify && empty('No filed figure for this period. Nothing is estimated or filled in.')}
        {d && (a.tool === 'get_trend' || a.tool === 'compare') && (
          <>
            {d.stats.length > 0 && <Rise i={1}><StatStrip stats={d.stats} goodWhenUp={goodWhenUp} /></Rise>}
            {d.chart && <Rise i={2}><ChartPanel data={d} /></Rise>}
            {d.table && <Rise i={3}><DataTable table={d.table} /></Rise>}
            {!d.table && empty('No filed figures for this request. Nothing is estimated or filled in.')}
          </>
        )}
        {d && a.tool === 'search_reports' && (
          d.passages.length
            ? <Rise i={1}><Passages passages={d.passages} query={(args.query as string) ?? a.question} /></Rise>
            : empty('Nothing relevant found in the loaded annual reports.')
        )}
        {a.errors.length > 0 && <Alert type="error" showIcon title={a.errors.join(' · ')} />}
        {d && <Sources sources={d.sources} notes={[...a.notes, ...d.notes]} />}
      </div>
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
      description={cmd ? <>To add it, run: <code style={{ fontFamily: 'var(--mono)', fontSize: 12.5 }}>{cmd}</code></> : undefined}
    />
  )
}

/** Light markdown: **bold** only; the LLM's text is otherwise plain. */
function renderAnswer(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') ? <strong key={i}>{part.slice(2, -2)}</strong> : part)
}
