import { Alert, Button, Card, Popconfirm, Progress, Segmented, Tag, Tooltip } from 'antd'
import {
  CheckCircleFilled, ClockCircleOutlined, CloseCircleFilled, DeploymentUnitOutlined, LoadingOutlined, PauseCircleOutlined,
  StopOutlined, ThunderboltOutlined,
} from '@ant-design/icons'
import type { EmbedDevice, EmbedJob, IndexInfo, ReportDoc } from '../lib/api'
import { embedding } from '../lib/embedJob'

const pct = (done: number, total: number) => (total ? Math.round((done / total) * 100) : 0)

/** Index summary + "Embed all pending", and the running job's progress, report by report. */
export function EmbeddingPanel({ index, job, device, setDevice, onEmbedAll, onStop }: {
  index: IndexInfo; job: EmbedJob | null; device: EmbedDevice; setDevice: (d: EmbedDevice) => void
  onEmbedAll: () => void; onStop: () => void
}) {
  const running = embedding(job)
  const share = pct(index.embedded, index.passages)
  const showJob = job && (running || (job.finished_at && Date.now() - new Date(job.finished_at).getTime() < 10 * 60e3))
  return (
    <Card className="embed-card" styles={{ body: { padding: 16 } }}>
      <div className="embed-head">
        <div className="embed-sum">
          <span className="prompt-icon"><DeploymentUnitOutlined /></span>
          <div>
            <div className="embed-title">Semantic search index</div>
            <div className="muted" style={{ fontSize: 12.5 }}>
              {index.passages
                ? <><b className="num">{index.embedded.toLocaleString('en-IN')}</b> of {index.passages.toLocaleString('en-IN')} report passages embedded{index.model ? ` · ${index.model}` : ''}</>
                : 'No report text yet: download a company’s annual reports first.'}
            </div>
          </div>
        </div>
        <Progress type="circle" size={46} percent={share} strokeWidth={9} status={share === 100 ? 'success' : 'normal'} />
        <div className="embed-actions">
          <Tooltip title="Where BGE-M3 runs. The GPU is faster but shared with the local LLM server; Auto falls back to the CPU when the GPU is full.">
            <Segmented<EmbedDevice>
              size="small" value={device} onChange={setDevice} disabled={running}
              options={[{ value: 'auto', label: 'Auto (GPU)' }, { value: 'cpu', label: 'CPU' }]}
            />
          </Tooltip>
          {running ? (
            <Popconfirm title="Stop embedding?" description="Reports already finished are kept; the next run continues." okText="Stop" cancelText="Keep going" onConfirm={onStop}>
              <Button danger icon={<StopOutlined />} disabled={job?.stop_requested}>{job?.stop_requested ? 'Stopping…' : 'Stop'}</Button>
            </Popconfirm>
          ) : (
            <Button type="primary" icon={<ThunderboltOutlined />} disabled={!index.reports_pending} onClick={onEmbedAll}>
              {index.reports_pending
                ? `Embed ${index.reports_pending} pending report${index.reports_pending === 1 ? '' : 's'}`
                : 'Everything embedded'}
            </Button>
          )}
        </div>
      </div>
      {index.reports_pending > 0 && !running && (
        <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
          {index.reports_pending} annual report{index.reports_pending === 1 ? '' : 's'} in {index.companies_pending} compan{index.companies_pending === 1 ? 'y' : 'ies'} can only be found by keyword search until embedded.
        </div>
      )}
      {showJob && job && <JobProgress job={job} />}
    </Card>
  )
}

function JobProgress({ job }: { job: EmbedJob }) {
  const total = job.items.reduce((n, i) => n + i.passages, 0)
  const done = job.items.reduce((n, i) => n + i.done, 0)
  const running = embedding(job)
  return (
    <div className="embed-job">
      <div className="embed-job-head">
        <b>{job.state === 'loading' ? 'Loading the embedding model…' : running ? 'Embedding' : job.state === 'done' ? 'Embedding finished' : job.state === 'stopped' ? 'Embedding stopped' : 'Embedding failed'}</b>
        <span className="muted">
          {job.message}{job.device ? ` · on ${job.device.startsWith('cuda') ? 'GPU' : 'CPU'}` : ''}
        </span>
      </div>
      <Progress percent={pct(done, total)} size="small" status={job.state === 'failed' ? 'exception' : job.state === 'done' ? 'success' : running ? 'active' : 'normal'}
        format={() => <span className="num">{done.toLocaleString('en-IN')}/{total.toLocaleString('en-IN')}</span>} />
      {job.error && <Alert type="error" showIcon title={job.error} style={{ margin: '8px 0' }} />}
      <div className="embed-items">
        {job.items.map((i) => (
          <div key={i.doc_id} className={`embed-item ${i.state}`}>
            <span className="embed-item-icon">
              {i.state === 'done' ? <CheckCircleFilled style={{ color: 'var(--up)' }} />
                : i.state === 'running' ? <LoadingOutlined />
                  : i.state === 'failed' ? <CloseCircleFilled style={{ color: 'var(--down)' }} />
                    : i.state === 'stopped' ? <PauseCircleOutlined />
                      : <ClockCircleOutlined />}
            </span>
            <span className="embed-item-name"><b>{i.ticker}</b> annual report {i.fy ?? ''}</span>
            <Progress percent={pct(i.done, i.passages)} size="small" showInfo={false} status={i.state === 'running' ? 'active' : i.state === 'done' ? 'success' : 'normal'} />
            <span className="num muted embed-item-count">{i.done.toLocaleString('en-IN')}/{i.passages.toLocaleString('en-IN')}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

/** One annual report in the table: its FY, green when embedded, amber when partly, grey when not. */
export function ReportTag({ r }: { r: ReportDoc }) {
  const full = r.passages > 0 && r.embedded >= r.passages
  const part = r.embedded > 0 && !full
  const title = `${r.fy ?? 'Report'}: ${r.passages.toLocaleString('en-IN')} passages, ${full ? 'all' : r.embedded.toLocaleString('en-IN')} embedded`
  return (
    <Tooltip title={title}>
      <Tag className={full ? 'tag-up' : part ? 'tag-part' : 'tag-none'} style={{ margin: 0 }}>
        {r.fy ?? '?'}{full ? ' ✓' : part ? ` ${pct(r.embedded, r.passages)}%` : ''}
      </Tag>
    </Tooltip>
  )
}
