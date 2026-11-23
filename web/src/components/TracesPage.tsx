import { useCallback, useEffect, useState } from 'react'
import {
  Alert, Button, Card, Col, Collapse, Descriptions, Drawer, Empty, Flex, Input, Row, Segmented, Select, Skeleton, Statistic,
  Table, Tag, Tooltip, Typography, theme,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ReloadOutlined } from '@ant-design/icons'
import { admin, type TraceDetail, type TraceSource, type TraceStats, type TraceSummary } from '../lib/api'
import { useAuth } from '../lib/auth'
import { useTokens } from '../lib/theme'
import { EChart } from '../charts/EChart'
import { dailyOption, stepsOption } from '../charts/options'
import { PageHeader } from './Chrome'
import { Rows } from './Rows'

const fmtMs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(ms >= 10000 ? 0 : 1)} s` : `${Math.round(ms)} ms`)
const pct = (n: number, d: number) => (d ? `${Math.round((n / d) * 100)}%` : '–')
const when = (iso: string) => new Date(iso).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })

const OUTCOME: Record<string, { color: string; label: string }> = {
  answered: { color: 'success', label: 'Answered' },
  rejected: { color: 'warning', label: 'Answer rejected' },
  clarify: { color: 'processing', label: 'Asked back' },
  empty: { color: 'default', label: 'No data' },
  failed: { color: 'error', label: 'Failed' },
}
const MODE: Record<string, string> = { llm: 'LLM', rules: 'Rules', explain: 'Why', search: 'Search' }

function OutcomeTag({ t }: { t: Pick<TraceSummary, 'outcome' | 'reject_reason' | 'error'> }) {
  const o = OUTCOME[t.outcome ?? ''] ?? { color: 'default', label: t.outcome ?? '–' }
  const tip = t.reject_reason ?? t.error
  const tag = <Tag color={o.color} style={{ margin: 0 }}>{o.label}</Tag>
  return tip ? <Tooltip title={tip}>{tag}</Tooltip> : tag
}

/** Admin -> Traces: one record per question, the path it took and what each step cost. */
export function TracesPage({ openId, onOpen }: { openId: string | null; onOpen: (id: string | null) => void }) {
  const { token } = theme.useToken()
  const t = useTokens()
  const [source, setSource] = useState<TraceSource>('api')
  const [days, setDays] = useState(7)
  const [mode, setMode] = useState<string | undefined>()
  const [outcome, setOutcome] = useState<string | undefined>()
  const [q, setQ] = useState('')
  const [stats, setStats] = useState<TraceStats | null>(null)
  const [rows, setRows] = useState<TraceSummary[]>([])
  const [next, setNext] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (before?: string) => {
    setLoading(true)
    setError(null)
    try {
      const [page, s] = await Promise.all([
        admin.traces({ source, days, mode, outcome, q: q || undefined, before, limit: 50 }),
        before ? Promise.resolve(null) : admin.traceStats(days, source),
      ])
      setRows((r) => (before ? [...r, ...page.items] : page.items))
      setNext(page.next_before)
      if (s) setStats(s)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally { setLoading(false) }
  }, [source, days, mode, outcome, q])

  useEffect(() => { void load() }, [load])

  const columns: ColumnsType<TraceSummary> = [
    { title: 'Time', dataIndex: 'created_at', width: 136, render: (v: string) => <Typography.Text type="secondary">{when(v)}</Typography.Text> },
    {
      title: 'Question', dataIndex: 'question', ellipsis: { showTitle: false },
      render: (v: string, r) => (
        <Tooltip title={r.read_as ? `Read as: ${r.read_as}` : v}>
          <Typography.Text>{v}</Typography.Text>
        </Tooltip>
      ),
    },
    { title: 'Route', dataIndex: 'mode', width: 92, render: (v: string | null) => (v ? <Tag style={{ margin: 0 }}>{MODE[v] ?? v}</Tag> : '–') },
    { title: 'Tool', dataIndex: 'tool', width: 140, render: (v: string | null) => <Typography.Text type="secondary">{v ?? '–'}</Typography.Text> },
    { title: 'Outcome', key: 'outcome', width: 150, render: (_, r) => <OutcomeTag t={r} /> },
    {
      title: 'LLM', key: 'llm', width: 150, align: 'right',
      render: (_, r) => (r.llm_calls
        ? (
          <Tooltip title={`${r.prompt_tokens.toLocaleString('en-IN')} prompt (${pct(r.cached_tokens, r.prompt_tokens)} cached) · ${r.completion_tokens.toLocaleString('en-IN')} written`}>
            <Typography.Text>{r.llm_calls} call{r.llm_calls > 1 ? 's' : ''} · {(r.prompt_tokens + r.completion_tokens).toLocaleString('en-IN')} tok</Typography.Text>
          </Tooltip>
        )
        : <Typography.Text type="secondary">–</Typography.Text>),
    },
    {
      title: 'Took', dataIndex: 'total_ms', width: 88, align: 'right',
      sorter: (a, b) => a.total_ms - b.total_ms,
      render: (v: number) => <span style={{ fontVariantNumeric: 'tabular-nums' }}>{fmtMs(v)}</span>,
    },
  ]

  return (
    <div style={{ maxWidth: 1400, margin: '0 auto' }}>
      <PageHeader
        title="Traces"
        description="One record per question: the path it took, what each step cost, and whether the written answer passed its check"
        extra={(
          <Flex gap="small" wrap>
            <Segmented<TraceSource>
              value={source} onChange={setSource}
              options={[{ value: 'api', label: 'Real use' }, { value: 'eval', label: 'Eval runs' }, { value: 'title', label: 'Chat titles' }]}
            />
            <Segmented<number> value={days} onChange={setDays} options={[{ value: 1, label: '24 h' }, { value: 7, label: '7 days' }, { value: 30, label: '30 days' }]} />
            <Tooltip title="Reload"><Button icon={<ReloadOutlined />} onClick={() => void load()} aria-label="Reload" /></Tooltip>
          </Flex>
        )}
      />
      <Flex vertical gap="middle">
        {error && <Alert type="error" showIcon title={error} />}

        <Row gutter={[16, 16]}>
          <Col xs={12} md={8} xl={4}><Card size="small"><Statistic title="Questions" value={stats?.questions ?? 0} loading={!stats} /></Card></Col>
          <Col xs={12} md={8} xl={4}>
            <Card size="small">
              <Statistic title="Understood by the LLM" value={stats ? pct(stats.llm, stats.questions) : '–'} loading={!stats} />
            </Card>
          </Col>
          <Col xs={12} md={8} xl={4}>
            <Card size="small">
              <Tooltip title="The model gave no valid call after two tries, or was unreachable: the rules planner answered">
                <Statistic title="Fell back to rules" value={stats ? pct(stats.fallback, stats.questions) : '–'} suffix={stats ? <Typography.Text type="secondary">· {stats.fallback}</Typography.Text> : null} loading={!stats} />
              </Tooltip>
            </Card>
          </Col>
          <Col xs={12} md={8} xl={4}>
            <Card size="small">
              <Tooltip title="Written answers that failed the check (a number not in the sources, an uncited page...): the data was shown instead">
                <Statistic title="Answers rejected" value={stats ? pct(stats.rejected, stats.questions) : '–'} suffix={stats ? <Typography.Text type="secondary">· {stats.rejected}</Typography.Text> : null} loading={!stats} />
              </Tooltip>
            </Card>
          </Col>
          <Col xs={12} md={8} xl={4}>
            <Card size="small">
              <Tooltip title="Half the questions were answered faster than p50; 95% faster than p95">
                <Statistic title="Time (p50)" value={stats ? fmtMs(stats.p50_ms) : '–'} suffix={stats ? <Typography.Text type="secondary">· p95 {fmtMs(stats.p95_ms)}</Typography.Text> : null} loading={!stats} />
              </Tooltip>
            </Card>
          </Col>
          <Col xs={12} md={8} xl={4}>
            <Card size="small">
              <Tooltip title="Prompt tokens served from llama.cpp's KV cache (the system prompt kept warm)">
                <Statistic title="Prompt cache hits" value={stats?.cache_rate != null ? `${Math.round(stats.cache_rate * 100)}%` : '–'} loading={!stats} />
              </Tooltip>
            </Card>
          </Col>
        </Row>

        <Row gutter={[16, 16]}>
          <Col xs={24} xl={10}>
            <Card size="small" title="Questions per day" style={{ height: '100%' }}>
              {stats?.per_day.length
                ? <EChart option={dailyOption(t, stats.per_day)} height={200} label="Questions per day" />
                : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No questions in this period" />}
            </Card>
          </Col>
          <Col xs={24} md={14} xl={8}>
            <Card size="small" title="Time per step" style={{ height: '100%' }}>
              <Table
                size="small" pagination={false} rowKey="kind" dataSource={stats?.steps ?? []} loading={!stats}
                columns={[
                  { title: 'Step', dataIndex: 'kind' },
                  { title: 'Runs', dataIndex: 'count', align: 'right' },
                  { title: 'p50', dataIndex: 'p50_ms', align: 'right', render: (v: number) => fmtMs(v) },
                  { title: 'p95', dataIndex: 'p95_ms', align: 'right', render: (v: number) => fmtMs(v) },
                ]}
              />
            </Card>
          </Col>
          <Col xs={24} md={10} xl={6}>
            <Card size="small" title="Why answers were rejected" style={{ height: '100%' }}>
              {stats?.reject_reasons.length
                ? (
                  <Rows
                    items={stats.reject_reasons}
                    rowKey={(r) => r.reason}
                    render={(r) => (
                      <Flex justify="space-between" gap="middle">
                        <Typography.Text ellipsis={{ tooltip: r.reason }}>{r.reason}</Typography.Text>
                        <Typography.Text strong>{r.count}</Typography.Text>
                      </Flex>
                    )}
                  />
                )
                : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="None rejected" />}
            </Card>
          </Col>
        </Row>

        <Card size="small">
          <Flex gap="small" wrap align="center">
            <Input.Search allowClear placeholder="Search questions" onSearch={setQ} style={{ width: 280 }} aria-label="Search questions" />
            <Select
              allowClear placeholder="Any route" value={mode} onChange={setMode} style={{ width: 150 }} aria-label="Route"
              options={Object.entries(MODE).map(([value, label]) => ({ value, label }))}
            />
            <Select
              allowClear placeholder="Any outcome" value={outcome} onChange={setOutcome} style={{ width: 180 }} aria-label="Outcome"
              options={Object.entries(OUTCOME).map(([value, o]) => ({ value, label: o.label }))}
            />
          </Flex>
        </Card>

        <Card size="small" styles={{ body: { padding: 0 } }}>
          <Table<TraceSummary>
            size="small" rowKey="id" columns={columns} dataSource={rows} loading={loading} pagination={false}
            scroll={{ x: 960 }}
            onRow={(r) => ({ onClick: () => onOpen(r.id), style: { cursor: 'pointer' } })}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No traces yet: ask a question" /> }}
          />
          {next && (
            <Flex justify="center" style={{ padding: token.padding }}>
              <Button onClick={() => void load(next)} loading={loading}>Load older</Button>
            </Flex>
          )}
        </Card>
      </Flex>

      <TraceDrawer id={openId} onClose={() => onOpen(null)} />
    </div>
  )
}

/** One trace: summary, a waterfall of its steps, and each step's details. */
function TraceDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { token } = theme.useToken()
  const t = useTokens()
  const { user } = useAuth()
  const [trace, setTrace] = useState<TraceDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    let live = true
    setTrace(null)
    setError(null)
    admin.trace(id).then((d) => live && setTrace(d)).catch((e: Error) => live && setError(e.message))
    return () => { live = false }
  }, [id])

  const steps = [...(trace?.steps ?? [])].sort((a, b) => a.start_ms - b.start_ms)
  const label = (s: TraceDetail['steps'][number]) => (s.kind === 'llm' ? `LLM call${s.parent ? ` · ${s.parent}` : ''}` : s.name.replace(/_/g, ' '))
  const own = trace?.chat_id && trace.user_id === user?.id

  return (
    <Drawer open={!!id} onClose={onClose} size="large" title={trace?.question ?? 'Trace'} destroyOnHidden>
      {error ? <Alert type="error" showIcon title={error} />
        : !trace ? <Skeleton active paragraph={{ rows: 8 }} />
          : (
            <Flex vertical gap="large">
              <Descriptions
                size="small" column={{ xs: 1, md: 2 }} bordered
                items={[
                  { key: 'when', label: 'When', children: when(trace.created_at) },
                  { key: 'took', label: 'Took', children: fmtMs(trace.total_ms) },
                  { key: 'route', label: 'Route', children: trace.mode ? MODE[trace.mode] ?? trace.mode : '–' },
                  { key: 'tool', label: 'Tool', children: trace.tool ?? '–' },
                  { key: 'outcome', label: 'Outcome', children: <OutcomeTag t={trace} /> },
                  { key: 'llm', label: 'LLM', children: trace.llm_calls ? `${trace.llm_calls} call${trace.llm_calls > 1 ? 's' : ''} · ${trace.llm_model}` : 'not used' },
                  {
                    key: 'tokens', label: 'Tokens',
                    children: trace.llm_calls ? `${trace.prompt_tokens.toLocaleString('en-IN')} prompt (${pct(trace.cached_tokens, trace.prompt_tokens)} cached) · ${trace.completion_tokens.toLocaleString('en-IN')} written` : '–',
                  },
                  { key: 'source', label: 'Source', children: trace.eval_run ? `eval · ${trace.eval_run}` : trace.source },
                  ...(trace.read_as ? [{ key: 'read', label: 'Read as', span: 'filled' as const, children: trace.read_as }] : []),
                  ...(trace.reject_reason ? [{ key: 'why', label: 'Rejected because', span: 'filled' as const, children: trace.reject_reason }] : []),
                  ...(trace.error ? [{ key: 'error', label: 'Error', span: 'filled' as const, children: <Typography.Text type="danger">{trace.error}</Typography.Text> }] : []),
                  ...(own ? [{ key: 'chat', label: 'Chat', span: 'filled' as const, children: <Typography.Link href={`#ask/${trace.chat_id}`}>Open the chat</Typography.Link> }] : []),
                ]}
              />
              <Card size="small" title="Steps" extra={<Typography.Text type="secondary">{steps.length} steps · bars show when each started and how long it took</Typography.Text>}>
                <EChart
                  option={stepsOption(t, steps.map((s) => ({ label: label(s), start: s.start_ms, ms: s.ms, error: s.status === 'error' })))}
                  height={steps.length * 30 + 48}
                  label="Steps of this request over time"
                />
              </Card>
              <Collapse
                size="small"
                items={steps.map((s, i) => ({
                  key: String(i),
                  label: (
                    <Flex gap="small" align="center" wrap>
                      <Typography.Text strong>{label(s)}</Typography.Text>
                      <Tag style={{ margin: 0 }}>{s.kind}</Tag>
                      {s.status === 'error' && <Tag color="error" style={{ margin: 0 }}>error</Tag>}
                      <Typography.Text type="secondary">{fmtMs(s.ms)}</Typography.Text>
                    </Flex>
                  ),
                  children: (
                    <pre style={{
                      margin: 0, padding: token.paddingSM, background: token.colorFillTertiary, borderRadius: token.borderRadius,
                      fontFamily: token.fontFamilyCode, fontSize: token.fontSizeSM, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                    }}
                    >
                      {JSON.stringify(s.detail, null, 2)}
                    </pre>
                  ),
                }))}
              />
            </Flex>
          )}
    </Drawer>
  )
}
