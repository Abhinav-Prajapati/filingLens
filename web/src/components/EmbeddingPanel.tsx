import { Alert, Button, Card, Col, Divider, Flex, Popconfirm, Progress, Row, Segmented, Space, Statistic, Tag, Tooltip, Typography, theme } from 'antd'
import {
  CheckCircleFilled, ClockCircleOutlined, CloseCircleFilled, LoadingOutlined, PauseCircleOutlined, StopOutlined, ThunderboltOutlined,
} from '@ant-design/icons'
import type { EmbedDevice, EmbedJob, IndexInfo, ReportDoc } from '../lib/api'
import { embedding } from '../lib/embedJob'
import { Rows } from './Rows'

const pct = (done: number, total: number) => (total ? Math.round((done / total) * 100) : 0)

/** Index summary + "Embed all pending", and the running job's progress, report by report. */
export function EmbeddingPanel({ index, job, device, setDevice, onEmbedAll, onStop }: {
  index: IndexInfo; job: EmbedJob | null; device: EmbedDevice; setDevice: (d: EmbedDevice) => void
  onEmbedAll: () => void; onStop: () => void
}) {
  const { token } = theme.useToken()
  const running = embedding(job)
  const share = pct(index.embedded, index.passages)
  const showJob = job && (running || (job.finished_at && Date.now() - new Date(job.finished_at).getTime() < 10 * 60e3))
  return (
    <Card
      title="Semantic search index"
      extra={
        <Space size="small" wrap>
          <Tooltip title="GPU: about 40 passages a second; the chat model is unloaded while the job runs, so questions are answered without the LLM until it ends. CPU: much slower, and chat keeps working.">
            <Segmented<EmbedDevice>
              size="small" value={device} onChange={setDevice} disabled={running}
              options={[{ value: 'auto', label: 'GPU' }, { value: 'cpu', label: 'CPU' }]}
            />
          </Tooltip>
          {running ? (
            <Popconfirm title="Stop embedding?" description="Reports already finished are kept; the next run continues." okText="Stop" cancelText="Keep going" onConfirm={onStop}>
              <Button danger icon={<StopOutlined />} disabled={job?.stop_requested}>{job?.stop_requested ? 'Stopping…' : 'Stop'}</Button>
            </Popconfirm>
          ) : (
            <Button type="primary" icon={<ThunderboltOutlined />} disabled={!index.reports_pending} onClick={onEmbedAll}>
              {index.reports_pending ? `Embed ${index.reports_pending} pending report${index.reports_pending === 1 ? '' : 's'}` : 'Everything embedded'}
            </Button>
          )}
        </Space>
      }
    >
      <Row gutter={[24, 16]} align="middle">
        <Col flex="none">
          <Progress type="circle" size={64} percent={share} status={share === 100 ? 'success' : 'normal'} />
        </Col>
        <Col flex="auto">
          <Row gutter={[24, 16]}>
            <Col xs={12} md={8}><Statistic title="Passages embedded" value={index.embedded} suffix={`/ ${index.passages.toLocaleString('en-IN')}`} /></Col>
            <Col xs={12} md={8}><Statistic title="Reports pending" value={index.reports_pending} suffix={index.companies_pending ? `in ${index.companies_pending} compan${index.companies_pending === 1 ? 'y' : 'ies'}` : undefined} /></Col>
            <Col xs={24} md={8}><Statistic title="Model" value={index.model ?? '–'} styles={{ content: { fontSize: token.fontSizeLG } }} /></Col>
          </Row>
        </Col>
      </Row>
      {index.reports_pending > 0 && !running && (
        <Typography.Paragraph type="secondary" style={{ margin: `${token.marginSM}px 0 0` }}>
          Reports not embedded can only be found by keyword search.
        </Typography.Paragraph>
      )}
      {showJob && job && <><Divider /><JobProgress job={job} /></>}
    </Card>
  )
}

function JobProgress({ job }: { job: EmbedJob }) {
  const { token } = theme.useToken()
  const total = job.items.reduce((n, i) => n + i.passages, 0)
  const done = job.items.reduce((n, i) => n + i.done, 0)
  const running = embedding(job)
  const heading = job.state === 'loading' ? 'Loading the embedding model' : running ? 'Embedding'
    : job.state === 'done' ? 'Embedding finished' : job.state === 'stopped' ? 'Embedding stopped' : 'Embedding failed'
  return (
    <Flex vertical gap="small">
      <Flex gap="small" align="center" wrap>
        <Typography.Text strong>{heading}</Typography.Text>
        <Typography.Text type="secondary">
          {job.message}{job.device ? ` · on ${/^(cuda|gpu)/.test(job.device) ? 'GPU' : 'CPU'}` : ''}
        </Typography.Text>
        {job.llm_paused && running && <Tag color="warning" style={{ margin: 0 }}>LLM paused</Tag>}
      </Flex>
      <Progress
        percent={pct(done, total)}
        status={job.state === 'failed' ? 'exception' : job.state === 'done' ? 'success' : running ? 'active' : 'normal'}
        format={() => `${done.toLocaleString('en-IN')} / ${total.toLocaleString('en-IN')}`}
      />
      {job.error && <Alert type="error" showIcon title={job.error} />}
      <Rows
        items={job.items}
        rowKey={(i) => i.doc_id}
        maxHeight={264}
        render={(i) => (
          <Flex align="center" gap="middle" style={{ width: '100%' }}>
            <span style={{ width: token.fontSize }}>
              {i.state === 'done' ? <CheckCircleFilled style={{ color: token.colorSuccess }} />
                : i.state === 'running' ? <LoadingOutlined style={{ color: token.colorPrimary }} />
                  : i.state === 'failed' ? <CloseCircleFilled style={{ color: token.colorError }} />
                    : i.state === 'stopped' ? <PauseCircleOutlined style={{ color: token.colorTextTertiary }} />
                      : <ClockCircleOutlined style={{ color: token.colorTextTertiary }} />}
            </span>
            <Typography.Text style={{ minWidth: 180 }} type={i.state === 'waiting' ? 'secondary' : undefined}>
              <Typography.Text strong>{i.ticker}</Typography.Text> annual report {i.fy ?? ''}
            </Typography.Text>
            <Progress percent={pct(i.done, i.passages)} size="small" showInfo={false} status={i.state === 'running' ? 'active' : i.state === 'done' ? 'success' : 'normal'} style={{ flex: 1, margin: 0 }} />
            <Typography.Text type="secondary" style={{ minWidth: 104, textAlign: 'right' }}>{i.done.toLocaleString('en-IN')} / {i.passages.toLocaleString('en-IN')}</Typography.Text>
          </Flex>
        )}
      />
    </Flex>
  )
}

/** One annual report in the table: its FY; success when embedded, processing when partly, plain when not. */
export function ReportTag({ r }: { r: ReportDoc }) {
  const full = r.passages > 0 && r.embedded >= r.passages
  const part = r.embedded > 0 && !full
  const title = `${r.fy ?? 'Report'}: ${r.passages.toLocaleString('en-IN')} passages, ${full ? 'all' : r.embedded.toLocaleString('en-IN')} embedded`
  return (
    <Tooltip title={title}>
      <Tag color={full ? 'success' : part ? 'processing' : 'default'} style={{ margin: 0 }}>
        {r.fy ?? '?'}{full ? ' ✓' : part ? ` ${pct(r.embedded, r.passages)}%` : ''}
      </Tag>
    </Tooltip>
  )
}
