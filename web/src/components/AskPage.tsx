import { useEffect, useRef, useState } from 'react'
import {
  Alert, Button, Card, Col, Flex, Input, Popconfirm, Row, Skeleton, Space, Statistic, Steps, Switch, Tag, Tooltip, Typography,
  theme, type InputRef,
} from 'antd'
import {
  BarChartOutlined, DeleteOutlined, FileSearchOutlined, LineChartOutlined, NumberOutlined, PieChartOutlined,
  RadarChartOutlined, SendOutlined, SwapOutlined, TranslationOutlined,
} from '@ant-design/icons'
import { api, ApiError, type AnswerView } from '../lib/api'
import { useMeta } from '../lib/meta'
import { AnswerCard } from './AnswerCard'
import { PageHeader } from './Chrome'

interface Turn { id: string; question: string; useLlm: boolean; answer?: AnswerView; error?: string }

const PROMPTS = [
  { q: 'Compare revenue of TCS, Infosys and HUL over the last 4 years', k: 'Comparison', icon: <SwapOutlined /> },
  { q: 'How has TCS net profit margin changed from FY23 to FY26?', k: 'Trend', icon: <LineChartOutlined /> },
  { q: 'Show Infosys quarterly net profit trend', k: 'Quarterly', icon: <BarChartOutlined /> },
  { q: 'What was Hindustan Unilever\'s effective tax rate in FY25?', k: 'Figure', icon: <NumberOutlined /> },
  { q: 'Compare total assets of TCS, Infosys and HUL in FY26', k: 'Share', icon: <PieChartOutlined /> },
  { q: 'Compare employee cost ratio of TCS and Infosys in FY26', k: 'Cost structure', icon: <RadarChartOutlined /> },
  { q: 'Infosys ka diluted EPS FY23 se FY26 tak kaise badla?', k: 'Hinglish', icon: <TranslationOutlined /> },
  { q: 'What are the key risks Infosys mentions in its annual report?', k: 'Report search', icon: <FileSearchOutlined /> },
]

export const THREAD_KEY = 'filinglens.thread.v1'
const load = (): Turn[] => {
  try { return (JSON.parse(localStorage.getItem(THREAD_KEY) ?? '[]') as Turn[]).filter((t) => t.answer || t.error) } catch { return [] }
}

export function AskPage() {
  const { token } = theme.useToken()
  const { health, loaded, companies } = useMeta()
  const [turns, setTurns] = useState<Turn[]>(load)
  const [q, setQ] = useState('')
  const [useLlm, setUseLlmState] = useState(() => {
    try { return localStorage.getItem('filinglens.llm') === '1' } catch { return false }
  })
  const setUseLlm = (v: boolean) => {
    setUseLlmState(v)
    try { localStorage.setItem('filinglens.llm', v ? '1' : '0') } catch { /* storage off */ }
  }
  const busy = turns.some((t) => !t.answer && !t.error)
  const input = useRef<InputRef>(null)
  const end = useRef<HTMLDivElement>(null)
  const abort = useRef<AbortController | null>(null)
  const llmAvailable = !!health?.llm

  useEffect(() => {
    try { localStorage.setItem(THREAD_KEY, JSON.stringify(turns.filter((t) => t.answer || t.error).slice(-20))) } catch { /* storage off */ }
  }, [turns])
  // ?q=… asks on load (shareable links); ?llm=1 uses the model for it
  useEffect(() => {
    const params = new URLSearchParams(location.search)
    const q0 = params.get('q')
    if (!q0) return
    history.replaceState(null, '', location.pathname + location.hash)
    void askWith(q0, params.get('llm') === '1' && llmAvailable)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName ?? '')) { e.preventDefault(); input.current?.focus() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const ask = (question: string) => { if (!busy) void askWith(question, useLlm && llmAvailable) }
  const askWith = async (question: string, llm: boolean) => {
    const text = question.trim()
    if (text.length < 2) return
    const last = [...turns].reverse().find((t) => t.answer)?.answer
    const previous = last ? { question: last.question, tool: last.tool } : undefined
    const id = Math.random().toString(36).slice(2)
    setTurns((ts) => [...ts, { id, question: text, useLlm: llm }])
    setQ('')
    requestAnimationFrame(() => end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }))
    abort.current = new AbortController()
    try {
      const answer = await api.ask(text, llm, abort.current.signal, previous)
      setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, answer } : t)))
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : e instanceof Error && e.name === 'AbortError' ? 'Stopped.' : String(e)
      setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, error: msg } : t)))
    }
  }

  const clear = () => {
    try { localStorage.removeItem(THREAD_KEY) } catch { /* storage off */ }
    setTurns([])
  }

  const fys = [...new Set(loaded.flatMap((c) => c.fys))].sort()
  const reports = loaded.reduce((n, c) => n + c.annual_reports.length, 0)

  return (
    <Flex vertical style={{ maxWidth: 1120, margin: '0 auto', minHeight: `calc(100vh - ${token.paddingLG * 2}px)` }}>
      <PageHeader
        title="Ask"
        description="Questions in English or Hinglish, answered from filed figures and annual reports"
        extra={
          <Space size="middle">
            <Tooltip title={llmAvailable ? `Model: ${health?.llm}` : 'No LLM configured: questions are understood by rules'}>
              <Space size="small">
                <Switch checked={useLlm && llmAvailable} disabled={!llmAvailable} onChange={setUseLlm} aria-label="Use the LLM" />
                <Typography.Text>LLM</Typography.Text>
              </Space>
            </Tooltip>
            <Popconfirm title="Clear the conversation?" okText="Clear" cancelText="Keep" onConfirm={clear} disabled={!turns.length}>
              <Button icon={<DeleteOutlined />} disabled={!turns.length}>Clear</Button>
            </Popconfirm>
          </Space>
        }
      />

      <Flex vertical gap="large" style={{ flex: 1 }}>
        {turns.length === 0 && (
          <>
            <Card>
              <Row gutter={[24, 16]}>
                <Col xs={12} md={6}><Statistic title="Companies loaded" value={loaded.length} suffix={`/ ${companies.length}`} /></Col>
                <Col xs={12} md={6}><Statistic title="Fiscal years" value={fys.length ? `${fys[0]}–${fys[fys.length - 1]}` : '–'} /></Col>
                <Col xs={12} md={6}><Statistic title="Filed facts" value={health?.facts ?? 0} /></Col>
                <Col xs={12} md={6}><Statistic title="Report passages" value={health?.chunks ?? 0} suffix={<Typography.Text type="secondary">· {reports} reports</Typography.Text>} /></Col>
              </Row>
            </Card>
            <Card title="Suggested questions">
              {PROMPTS.map((p) => (
                <Card.Grid
                  key={p.q}
                  style={{ width: '25%', minWidth: 220, cursor: 'pointer' }}
                  role="button"
                  tabIndex={0}
                  onClick={() => ask(p.q)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); ask(p.q) } }}
                >
                  <Flex vertical gap="small">
                    <Space size="small">
                      <Typography.Text type="secondary">{p.icon}</Typography.Text>
                      <Tag>{p.k}</Tag>
                    </Space>
                    <Typography.Text>{p.q}</Typography.Text>
                  </Flex>
                </Card.Grid>
              ))}
            </Card>
          </>
        )}

        {turns.map((t) => (
          <Flex key={t.id} vertical gap="middle">
            <Flex justify="flex-end">
              <Card size="small" style={{ maxWidth: '80%', background: token.colorPrimaryBg, borderColor: token.colorPrimaryBorder }}>
                <Typography.Text>{t.question}</Typography.Text>
              </Card>
            </Flex>
            {t.answer ? <AnswerCard a={t.answer} /> : t.error
              ? <Alert type="error" showIcon title={t.error} />
              : <Pending llm={t.useLlm} onStop={() => abort.current?.abort()} />}
          </Flex>
        ))}
        <div ref={end} />
      </Flex>

      <div style={{ position: 'sticky', bottom: 0, background: token.colorBgLayout, paddingBlock: token.padding, marginTop: token.marginLG }}>
        <form onSubmit={(e) => { e.preventDefault(); ask(q) }}>
          <Space.Compact block size="large">
            <Input
              ref={input} value={q} onChange={(e) => setQ(e.target.value)} autoFocus
              placeholder="e.g. TCS vs Infosys net margin last 3 years" aria-label="Ask a question"
            />
            <Button type="primary" htmlType="submit" icon={<SendOutlined />} disabled={busy || q.trim().length < 2}>Ask</Button>
          </Space.Compact>
        </form>
        <Flex justify="space-between" style={{ marginTop: token.marginXS }}>
          <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM }}>
            {useLlm && llmAvailable ? 'The LLM reads the question and writes a summary; numbers still come from the filings.' : 'Rules mode: instant, no model involved.'}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM }}>Press <Typography.Text keyboard>/</Typography.Text> to focus</Typography.Text>
        </Flex>
      </div>
    </Flex>
  )
}

function Pending({ llm, onStop }: { llm: boolean; onStop: () => void }) {
  const { token } = theme.useToken()
  const [step, setStep] = useState(0)
  useEffect(() => {
    const ids = [setTimeout(() => setStep(1), 700), setTimeout(() => setStep(2), llm ? 2600 : 1600)]
    return () => ids.forEach(clearTimeout)
  }, [llm])
  return (
    <Card extra={<Button size="small" onClick={onStop}>Stop</Button>} title="Working on it">
      <Steps
        size="small"
        current={step}
        items={[
          { title: 'Reading the question' },
          { title: 'Fetching filed figures' },
          { title: llm ? 'Writing the summary' : 'Laying out charts' },
        ]}
      />
      <Skeleton active title={false} paragraph={{ rows: 3 }} style={{ marginTop: token.marginLG }} />
    </Card>
  )
}
