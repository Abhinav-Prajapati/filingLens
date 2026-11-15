import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { Alert, Button, Card, Input, Skeleton, Statistic, Steps, Switch, Tag, Tooltip, type InputRef } from 'antd'
import {
  ArrowUpOutlined, BarChartOutlined, FileSearchOutlined, LineChartOutlined, NumberOutlined, PieChartOutlined, RadarChartOutlined,
  RobotOutlined, SwapOutlined, TranslationOutlined,
} from '@ant-design/icons'
import { api, ApiError, type AnswerView } from '../lib/api'
import { useMeta } from '../lib/meta'
import { AnswerCard } from './AnswerCard'

interface Turn { id: string; question: string; useLlm: boolean; answer?: AnswerView; error?: string }

const PROMPTS = [
  { q: 'Compare revenue of TCS, Infosys and HUL over the last 4 years', k: 'Compare · slope · heatmap', icon: <SwapOutlined /> },
  { q: 'How has TCS net profit margin changed from FY23 to FY26?', k: 'Margin trend', icon: <LineChartOutlined /> },
  { q: 'Show Infosys quarterly net profit trend', k: 'Quarterly', icon: <BarChartOutlined /> },
  { q: 'What was Hindustan Unilever\'s effective tax rate in FY25?', k: 'Figure · peers', icon: <NumberOutlined /> },
  { q: 'Compare total assets of TCS, Infosys and HUL in FY26', k: 'Ranked · share', icon: <PieChartOutlined /> },
  { q: 'Compare employee cost ratio of TCS and Infosys in FY26', k: 'Cost structure', icon: <RadarChartOutlined /> },
  { q: 'Infosys ka diluted EPS FY23 se FY26 tak kaise badla?', k: 'Hinglish', icon: <TranslationOutlined /> },
  { q: 'What are the key risks Infosys mentions in its annual report?', k: 'Report search', icon: <FileSearchOutlined /> },
]

export const THREAD_KEY = 'filinglens.thread.v1'
const load = (): Turn[] => {
  try { return (JSON.parse(localStorage.getItem(THREAD_KEY) ?? '[]') as Turn[]).filter((t) => t.answer || t.error) } catch { return [] }
}

export function AskPage() {
  const { health, loaded, companies } = useMeta()
  const [turns, setTurns] = useState<Turn[]>(load)
  const [q, setQ] = useState('')
  const [useLlm, setUseLlmState] = useState(() => {
    try { return localStorage.getItem('filinglens.llm') === '1' } catch { return false }
  })
  const setUseLlm = (f: (v: boolean) => boolean) => setUseLlmState((v) => {
    const n = f(v)
    try { localStorage.setItem('filinglens.llm', n ? '1' : '0') } catch { /* storage off */ }
    return n
  })
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
    const llm = params.get('llm') === '1' && llmAvailable
    void askWith(q0, llm)
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

  const fys = [...new Set(loaded.flatMap((c) => c.fys))].sort()
  const reports = loaded.reduce((n, c) => n + c.annual_reports.length, 0)

  return (
    <>
      <div className="page thread" style={{ width: '100%' }}>
        {turns.length === 0 && (
          <motion.section className="intro" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5 }}>
            <Tag variant="outlined" color="gold" style={{ fontFamily: 'var(--mono)', fontSize: 11, letterSpacing: '0.06em' }}>RESEARCH DESK</Tag>
            <h2>Ask about the filings.<br /><span>Get the figures, <em>charted and sourced.</em></span></h2>
            <p className="intro-lede">
              Every number comes from the XBRL results and annual reports, never from the model. Ask in English or Hinglish;
              each answer carries its page references, and a figure that was not filed stays blank instead of being estimated.
            </p>
            <div className="intro-facts">
              <Statistic title="Companies loaded" value={loaded.length} suffix={<span className="muted" style={{ fontSize: 13 }}>of {companies.length}</span>} />
              <Statistic title="Fiscal years" value={fys.length ? `${fys[0]}–${fys[fys.length - 1]}` : '–'} />
              <Statistic title="Filed facts" value={health?.facts ?? 0} formatter={(v) => Number(v).toLocaleString('en-IN')} />
              <Statistic title="Report passages" value={health?.chunks ?? 0} formatter={(v) => Number(v).toLocaleString('en-IN')} suffix={<span className="muted" style={{ fontSize: 13 }}>{reports} reports</span>} />
            </div>
            <div className="eyebrow" style={{ marginBottom: 10 }}>Try one</div>
            <div className="prompt-grid">
              {PROMPTS.map((p, i) => (
                <motion.div key={p.q} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.12 + i * 0.04 }}>
                  <Card
                    hoverable size="small" className="prompt-card" role="button" tabIndex={0}
                    onClick={() => ask(p.q)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); ask(p.q) } }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span className="prompt-icon">{p.icon}</span>
                      <span className="eyebrow" style={{ fontSize: 10 }}>{p.k}</span>
                    </div>
                    <span className="prompt-q">{p.q}</span>
                  </Card>
                </motion.div>
              ))}
            </div>
          </motion.section>
        )}
        <AnimatePresence initial={false}>
          {turns.map((t) => (
            <motion.div key={t.id} className="turn" layout="position" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
              <div className="qrow"><div className="qbubble">{t.question}</div></div>
              {t.answer ? <AnswerCard a={t.answer} /> : t.error
                ? <Alert type="error" showIcon title={t.error} />
                : <Pending llm={t.useLlm} onStop={() => abort.current?.abort()} />}
            </motion.div>
          ))}
        </AnimatePresence>
        <div ref={end} />
      </div>
      <div className="askbar-wrap">
        <div className="page">
          <form className="askbar" onSubmit={(e) => { e.preventDefault(); ask(q) }}>
            <Input
              ref={input} value={q} onChange={(e) => setQ(e.target.value)} autoFocus variant="borderless"
              placeholder="e.g. TCS vs Infosys net margin last 3 years" aria-label="Ask a question"
            />
            <Tooltip title={llmAvailable ? `Model: ${health?.llm}` : 'No LLM configured: questions are understood by rules'}>
              <label className="llm">
                <RobotOutlined />
                <Switch size="small" checked={useLlm && llmAvailable} disabled={!llmAvailable} onChange={() => setUseLlm((v) => !v)} aria-label="Use the LLM" />
              </label>
            </Tooltip>
            <Button type="primary" htmlType="submit" shape="circle" icon={<ArrowUpOutlined />} disabled={busy || q.trim().length < 2} aria-label="Ask" />
          </form>
          <div className="ask-hint">
            <span>{useLlm && llmAvailable ? 'LLM reads the question and writes a summary; numbers still come from the database' : 'Rules mode: instant, no model involved'}</span>
            <span className="mono">/ to focus</span>
          </div>
        </div>
      </div>
    </>
  )
}

function Pending({ llm, onStop }: { llm: boolean; onStop: () => void }) {
  const [step, setStep] = useState(0)
  useEffect(() => {
    const ids = [setTimeout(() => setStep(1), 700), setTimeout(() => setStep(2), llm ? 2600 : 1600)]
    return () => ids.forEach(clearTimeout)
  }, [llm])
  return (
    <Card size="small" styles={{ body: { padding: '16px 20px' } }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <Steps
          size="small"
          type="dot"
          current={step}
          style={{ flex: 1 }}
          items={[
            { title: 'Reading the question' },
            { title: 'Fetching filed figures' },
            { title: llm ? 'Writing the summary' : 'Laying out charts' },
          ]}
        />
        <Button size="small" type="text" onClick={onStop}>Stop</Button>
      </div>
      <Skeleton active title={false} paragraph={{ rows: 3, width: ['92%', '78%', '55%'] }} style={{ marginTop: 16 }} />
    </Card>
  )
}
