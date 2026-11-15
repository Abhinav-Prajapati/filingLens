import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, App as AntApp, Button, Card, Descriptions, Drawer, Empty, Input, Progress, Segmented, Select, Skeleton, Steps, Table, Tag,
  Tooltip, Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  CheckCircleFilled, CloudDownloadOutlined, CloudServerOutlined, CloudUploadOutlined, DatabaseOutlined, DownloadOutlined,
  FileTextOutlined, ReloadOutlined, SearchOutlined, ThunderboltOutlined,
} from '@ant-design/icons'
import {
  admin, type EmbedDevice, type EmbedJob, type Job, type ListedCompanies, type ListedRow, type NSEAvailability, type StoredFile,
} from '../lib/api'
import { embedding, useEmbedJob } from '../lib/embedJob'
import { EmbeddingPanel, ReportTag } from './EmbeddingPanel'
import { useMeta } from '../lib/meta'
import { Rise } from './bits'

type Show = 'all' | 'tracked' | 'xbrl' | 'reports' | 'unembedded' | 'stored' | 'untracked'

const hasData = (r: ListedRow) => r.xbrl_files > 0 || r.fys.length > 0 || r.annual_reports.length > 0
const needsEmbedding = (r: ListedRow) => r.reports.some((x) => x.embedded < x.passages)

const SHOW: { value: Show; label: string; test: (r: ListedRow) => boolean }[] = [
  { value: 'all', label: 'All', test: () => true },
  { value: 'tracked', label: 'Tracked', test: (r) => r.tracked },
  { value: 'xbrl', label: 'Has XBRL', test: (r) => r.xbrl_files > 0 || r.fys.length > 0 },
  { value: 'reports', label: 'Has reports', test: (r) => r.annual_reports.length > 0 },
  { value: 'unembedded', label: 'PDF not embedded', test: needsEmbedding },
  { value: 'stored', label: 'In S3', test: (r) => r.stored > 0 },
  { value: 'untracked', label: 'Not tracked', test: (r) => !r.tracked },
]

const ACTIVE: Job['stage'][] = ['queued', 'listing', 'downloading', 'ingesting']
const isActive = (j?: Job) => !!j && ACTIVE.includes(j.stage)

const fySpan = (fys: string[]) => (fys.length ? (fys.length > 1 ? `${fys[0]}–${fys[fys.length - 1]}` : fys[0]) : '')
const date = (iso: string | null) =>
  iso ? new Date(iso).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '–'
const size = (n: number) => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`)
const pct = (j: Job) => (j.stage === 'done' ? 100 : j.stage === 'ingesting' ? 95 : j.total ? Math.round((j.done / j.total) * 90) : 3)

/** Download jobs of this server session, polled while any is running. */
function useJobs(onFinished: (j: Job) => void) {
  const [jobs, setJobs] = useState<Record<string, Job>>({})
  const seen = useRef<Record<string, Job['stage']>>({})
  const finished = useRef(onFinished)
  finished.current = onFinished

  const load = useCallback(async () => {
    const list = await admin.jobs()
    const next: Record<string, Job> = {}
    for (const j of list) {
      next[j.symbol] = j
      const before = seen.current[j.symbol]
      if (before && ACTIVE.includes(before) && !ACTIVE.includes(j.stage)) finished.current(j)
      seen.current[j.symbol] = j.stage
    }
    setJobs(next)
    return list.some((j) => ACTIVE.includes(j.stage))
  }, [])

  const [polling, setPolling] = useState(true)
  useEffect(() => {
    if (!polling) return
    let live = true
    let timer: number | undefined
    const tick = async () => {
      let busy = false
      try { busy = await load() } catch { /* keep polling */ }
      if (!live) return
      if (busy) timer = window.setTimeout(tick, 1200)
      else setPolling(false)
    }
    tick()
    return () => { live = false; window.clearTimeout(timer) }
  }, [polling, load])

  const start = useCallback(async (symbol: string) => {
    const j = await admin.download(symbol)
    seen.current[j.symbol] = j.stage
    setJobs((js) => ({ ...js, [j.symbol]: j }))
    setPolling(true)
    return j
  }, [])

  return { jobs, start }
}

/** Admin → Data: every NSE-listed equity, what this install holds for it (database and S3
 *  bucket), a live check of what NSE has, and downloads (NSE → bucket → ingest). */
export function AdminData() {
  const { message } = AntApp.useApp()
  const { refresh: refreshMeta } = useMeta()
  const [data, setData] = useState<ListedCompanies | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [show, setShow] = useState<Show>('all')
  const [series, setSeries] = useState<string>('all')
  const [open, setOpen] = useState<ListedRow | null>(null)

  const reload = useCallback(() => {
    admin.companies().then((d) => { setData(d); setError(null) }).catch((e: Error) => setError(e.message))
  }, [])
  useEffect(reload, [reload])

  const { jobs, start } = useJobs((j) => {
    reload()
    refreshMeta()                                  // the sidebar and Ask page see the new data
    if (j.stage === 'done') message.success(`${j.symbol}: ${j.message}${j.facts ? ` · ${j.facts} figures` : ''}`)
    else message.error(`${j.symbol}: ${j.error ?? 'download failed'}`)
  })

  const download = useCallback((r: ListedRow) => {
    start(r.symbol).catch((e: Error) => message.error(`${r.symbol}: ${e.message}`))
  }, [start, message])

  const [device, setDevice] = useState<EmbedDevice>('auto')
  const embedJob = useEmbedJob((j) => {
    reload()
    if (j.state === 'done') message.success(`Embedding finished: ${j.message}`)
    else if (j.state === 'stopped') message.info(`Embedding stopped: ${j.message}`)
    else message.error(`Embedding failed: ${j.error ?? j.message}`)
  })
  const embed = useCallback((body: { doc_ids?: string[]; tickers?: string[] }, what: string) => {
    embedJob.start({ ...body, device })
      .then((j) => message.info(`Embedding ${j.items.length} report${j.items.length === 1 ? '' : 's'}${what ? ` of ${what}` : ''}`))
      .catch((e: Error) => message.error(e.message))
  }, [embedJob, device, message])
  const embedRunning = embedding(embedJob.job)

  // keep the open drawer's row in sync with reloaded data
  const openRow = open && data ? data.rows.find((r) => r.symbol === open.symbol) ?? open : open

  const rows = useMemo(() => {
    if (!data) return []
    const q = query.trim().toLowerCase()
    const test = SHOW.find((s) => s.value === show)!.test
    return data.rows.filter((r) => test(r) && (series === 'all' || r.series === series)
      && (!q || r.symbol.toLowerCase().includes(q) || r.name.toLowerCase().includes(q) || r.isin.toLowerCase() === q))
  }, [data, query, show, series])

  const counts = useMemo(() => Object.fromEntries(SHOW.map((s) => [s.value, data?.rows.filter(s.test).length ?? 0])), [data])
  const seriesOptions = useMemo(() => {
    const n = new Map<string, number>()
    data?.rows.forEach((r) => n.set(r.series, (n.get(r.series) ?? 0) + 1))
    return [{ value: 'all', label: 'All series' }, ...[...n.entries()].sort((a, b) => b[1] - a[1]).map(([s, c]) => ({ value: s, label: `${s} · ${c}` }))]
  }, [data])
  const running = Object.values(jobs).filter(isActive)

  if (error && !data) return <div className="page wide"><Alert type="error" showIcon title="Couldn’t load the company list" description={error} /></div>
  if (!data) return <div className="page wide"><Skeleton active paragraph={{ rows: 12 }} /></div>
  if (!data.total) {
    return (
      <div className="page wide">
        <Card>
          <Empty description={<>NSE’s equity list hasn’t been downloaded yet. Run <code>filinglens companies --refresh</code>, then reload.</>} />
        </Card>
      </div>
    )
  }

  const columns: ColumnsType<ListedRow> = [
    {
      title: 'Company', key: 'symbol', fixed: 'left', width: 220,
      sorter: (a, b) => a.symbol.localeCompare(b.symbol), defaultSortOrder: 'ascend',
      render: (_, r) => (
        <div className="co-cell">
          <span className="co-sym">{r.symbol}</span>
          <span className="co-full" title={r.name}>{r.name}</span>
        </div>
      ),
    },
    {
      title: 'Series · listed', dataIndex: 'listed_on', width: 120,
      sorter: (a, b) => (a.listed_on ?? '').localeCompare(b.listed_on ?? ''),
      render: (d: string | null, r) => (
        <div className="hold">
          <Tag style={{ margin: 0, width: 'fit-content' }} className="mono">{r.series}</Tag>
          <span className="muted num" style={{ fontSize: 12 }}>{date(d)}</span>
        </div>
      ),
    },
    {
      title: 'Status', key: 'tracked', width: 104,
      sorter: (a, b) => Number(b.tracked) - Number(a.tracked),
      render: (_, r) => (r.tracked
        ? <Tag color="gold" style={{ margin: 0 }}>Tracked</Tag>
        : <span className="muted" style={{ fontSize: 12.5 }}>Not tracked</span>),
    },
    {
      title: <Tooltip title="Ingested XBRL results: the years with figures, the latest quarter, and files downloaded but not ingested yet">XBRL figures</Tooltip>,
      key: 'xbrl', width: 170,
      sorter: (a, b) => a.fys.length - b.fys.length || a.xbrl_files - b.xbrl_files,
      render: (_, r) => (
        <div className="hold">
          {r.fys.length || r.xbrl_files
            ? <span><b>{fySpan(r.fys) || 'quarters only'}</b> <span className="muted">· {r.xbrl_files} file{r.xbrl_files === 1 ? '' : 's'}</span></span>
            : <span className="muted">–</span>}
          {r.latest_quarter && <span className="muted">latest {r.latest_quarter}</span>}
          {r.pending_xbrl > 0 && <span className="pending">{r.pending_xbrl} downloaded, not ingested</span>}
        </div>
      ),
    },
    {
      title: <Tooltip title="Ingested annual-report PDFs. ✓ = embedded for semantic search; a % = partly; plain = keyword search only">Annual reports</Tooltip>,
      key: 'reports', width: 180,
      sorter: (a, b) => a.annual_reports.length - b.annual_reports.length,
      render: (_, r) => (
        <div className="hold">
          {r.reports.length
            ? <span style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>{r.reports.map((x) => <ReportTag key={x.doc_id} r={x} />)}</span>
            : r.annual_reports.length
              ? <span style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>{r.annual_reports.map((fy) => <Tag key={fy} style={{ margin: 0 }}>{fy}</Tag>)}</span>
              : <span className="muted">–</span>}
          {r.pending_reports > 0 && <span className="pending">{r.pending_reports} downloaded, not ingested</span>}
        </div>
      ),
    },
    {
      title: <Tooltip title="Files kept in the S3 bucket">In S3</Tooltip>, dataIndex: 'stored', width: 80, align: 'right',
      sorter: (a, b) => a.stored - b.stored,
      render: (n: number) => (n ? <span className="num">{n}</span> : <span className="muted">–</span>),
    },
    {
      title: 'Passages', dataIndex: 'passages', width: 96, align: 'right',
      sorter: (a, b) => a.passages - b.passages,
      render: (n: number) => (n ? <span className="num">{n.toLocaleString('en-IN')}</span> : <span className="muted">–</span>),
    },
    {
      title: '', key: 'actions', width: 150, fixed: 'right',
      render: (_, r) => {
        const j = jobs[r.symbol]
        if (isActive(j)) {
          return (
            <button type="button" className="row-job" onClick={() => setOpen(r)} title="Show progress">
              <Progress percent={pct(j!)} size="small" showInfo={false} status="active" />
              <span>{j!.stage === 'queued' ? 'Queued' : j!.stage === 'downloading' ? `${j!.done}/${j!.total} files` : j!.stage === 'ingesting' ? 'Ingesting' : 'Asking NSE'}</span>
            </button>
          )
        }
        return (
          <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
            {!hasData(r) && (
              <Tooltip title="Download every filing NSE has, save it to S3 and ingest it">
                <Button size="small" type="primary" icon={<DownloadOutlined />} onClick={() => download(r)}>Download</Button>
              </Tooltip>
            )}
            {needsEmbedding(r) && (
              <Tooltip title={embedRunning ? 'An embedding job is running' : 'Embed this company’s annual reports for semantic search'}>
                <Button size="small" icon={<ThunderboltOutlined />} disabled={embedRunning} onClick={() => embed({ tickers: [r.symbol] }, r.symbol)} aria-label={`Embed ${r.symbol}'s annual reports`} />
              </Tooltip>
            )}
            <Tooltip title={hasData(r) ? 'Check NSE, reports, files' : 'Details'}>
              <Button size="small" icon={<CloudServerOutlined />} onClick={() => setOpen(r)} aria-label={`Open ${r.symbol}`} />
            </Tooltip>
          </div>
        )
      },
    },
  ]

  const tiles = [
    { icon: <DatabaseOutlined />, label: 'Listed on NSE', value: data.total, foot: data.list_updated ? `list from ${date(data.list_updated)}` : '' },
    { icon: <SearchOutlined />, label: 'Tracked', value: counts.tracked, foot: 'in the company list, can be asked about' },
    { icon: <CloudDownloadOutlined />, label: 'With XBRL figures', value: counts.xbrl, foot: 'results ingested' },
    { icon: <CloudUploadOutlined />, label: 'In S3', value: counts.stored, foot: data.storage ? 'companies with files in the bucket' : 'no bucket configured' },
  ]

  return (
    <div className="page wide">
      <div className="admin-tiles">
        {tiles.map((t, i) => (
          <Rise key={t.label} i={i}>
            <Card className="admin-tile">
              <div className="admin-tile-head"><span className="prompt-icon">{t.icon}</span>{t.label}</div>
              <div className="admin-tile-num num">{t.value.toLocaleString('en-IN')}</div>
              <div className="muted" style={{ fontSize: 12 }}>{t.foot}</div>
            </Card>
          </Rise>
        ))}
      </div>

      {!data.storage && <Alert type="warning" showIcon style={{ marginBottom: 14 }} title="No S3 bucket configured" description="Downloads are kept in the local inbox only. Set FILINGLENS_S3_* in .env and start the stack with docker compose." />}
      {running.length > 0 && (
        <Alert
          type="info" showIcon style={{ marginBottom: 14 }}
          title={`Downloading ${running.map((j) => j.symbol).join(', ')}`}
          description="Runs one company at a time (NSE limits request rates). You can keep working; the list updates when each finishes."
        />
      )}

      <EmbeddingPanel index={data.index} job={embedJob.job} device={device} setDevice={setDevice}
        onEmbedAll={() => embed({}, '')} onStop={() => { embedJob.stop().catch((e: Error) => message.error(e.message)) }} />

      <div className="toolbar">
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="Search symbol, name or ISIN"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ width: 280 }}
          aria-label="Search companies"
        />
        <Segmented<Show>
          value={show}
          onChange={setShow}
          options={SHOW.map((s) => ({ value: s.value, label: <span>{s.label} <span className="muted num">{counts[s.value].toLocaleString('en-IN')}</span></span> }))}
        />
        <Select value={series} onChange={setSeries} options={seriesOptions} style={{ width: 140 }} aria-label="NSE series" />
        <span className="muted" style={{ marginLeft: 'auto', fontSize: 12.5 }}>{rows.length.toLocaleString('en-IN')} shown</span>
      </div>

      <Card styles={{ body: { padding: 0 } }}>
        <Table<ListedRow>
          className="data-table admin-table"
          size="small"
          rowKey="symbol"
          columns={columns}
          dataSource={rows}
          scroll={{ x: 1170 }}
          showSorterTooltip={false}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={query ? `No listed company matches “${query}”` : 'Nothing here'} /> }}
          pagination={{ defaultPageSize: 25, pageSizeOptions: [25, 50, 100], showSizeChanger: true, size: 'small', showTotal: (n, [a, b]) => `${a}–${b} of ${n.toLocaleString('en-IN')}` }}
          onRow={(r) => ({ onDoubleClick: () => setOpen(r) })}
        />
      </Card>

      <CompanyDrawer row={openRow} job={openRow ? jobs[openRow.symbol] : undefined} storage={data.storage}
        onDownload={download} onClose={() => setOpen(null)}
        embedJob={embedJob.job} onEmbed={(docIds) => openRow && embed({ doc_ids: docIds }, openRow.symbol)} />
    </div>
  )
}

/** One company: what NSE has (live), the download job, and its files in the bucket. */
function CompanyDrawer({ row, job, storage, onDownload, onClose, embedJob, onEmbed }: {
  row: ListedRow | null; job?: Job; storage: boolean; onDownload: (r: ListedRow) => void; onClose: () => void
  embedJob: EmbedJob | null; onEmbed: (docIds: string[]) => void
}) {
  const [res, setRes] = useState<NSEAvailability | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [files, setFiles] = useState<StoredFile[] | null>(null)
  const ctl = useRef<AbortController | null>(null)
  const sym = row?.symbol ?? ''

  const check = useCallback((symbol: string, refresh = false) => {
    ctl.current?.abort()
    const c = new AbortController()
    ctl.current = c
    setBusy(true); setError(null); setRes(null)
    admin.nse(symbol, refresh, c.signal)
      .then((r) => { if (!c.signal.aborted) setRes(r) })
      .catch((e: Error) => { if (!c.signal.aborted) setError(e.message) })
      .finally(() => { if (!c.signal.aborted) setBusy(false) })
  }, [])

  const loadFiles = useCallback((symbol: string) => {
    if (!storage) return
    admin.files(symbol).then(setFiles).catch(() => setFiles([]))
  }, [storage])

  useEffect(() => {
    if (!sym) return
    setFiles(null)
    check(sym)
    loadFiles(sym)
    return () => ctl.current?.abort()
  }, [sym, check, loadFiles])

  // a job for this company just finished: refresh what NSE has vs. what is held, and the files
  const stage = job?.stage
  const prevStage = useRef(stage)
  useEffect(() => {
    if (sym && prevStage.current && ACTIVE.includes(prevStage.current) && stage && !ACTIVE.includes(stage)) {
      check(sym, true)
      loadFiles(sym)
    }
    prevStage.current = stage
  }, [stage, sym, check, loadFiles])

  const active = isActive(job)
  const cmd = `filinglens download ${sym} --annual-reports && filinglens ingest work/inbox`
  const newFiles = res?.new_files ?? 0

  return (
    <Drawer
      open={!!row}
      onClose={onClose}
      size="large"
      title={row && (
        <div>
          <div className="eyebrow">{row.tracked ? 'Tracked' : 'Not tracked'} · {row.series} · {row.isin}</div>
          <div style={{ fontSize: 16, fontWeight: 600, marginTop: 2 }}>{row.name} <span className="muted mono" style={{ fontSize: 13 }}>{row.symbol}</span></div>
        </div>
      )}
      extra={<Button icon={<ReloadOutlined />} onClick={() => check(sym, true)} disabled={busy || active}>Check NSE again</Button>}
    >
      {row && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <DownloadPanel row={row} job={job} newFiles={newFiles} checked={!!res} storage={storage} onDownload={() => onDownload(row)} />

          {busy && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <Steps
                orientation="vertical" size="small" type="dot" current={1}
                items={[{ title: 'Connect to NSE' }, { title: 'List XBRL results (annual and quarterly)' }, { title: 'List annual reports' }]}
              />
              <p className="muted" style={{ margin: 0 }}>NSE is asked slowly on purpose, so this takes about 10 seconds{active ? ' (longer while a download is running)' : ''}.</p>
              <Skeleton active paragraph={{ rows: 4 }} title={false} />
            </div>
          )}
          {error && <Alert type="error" showIcon title="NSE check failed" description={error} action={<Button size="small" onClick={() => check(sym, true)}>Retry</Button>} />}
          {res && (
            <>
              {res.warning && <Alert type="warning" showIcon title={res.warning} />}
              <Descriptions
                size="small" column={1} bordered
                items={[
                  { key: 'x', label: 'XBRL results on NSE', children: res.xbrl_filings ? <b>{res.xbrl_filings} filings</b> : <span className="muted">none in the last 5 years</span> },
                  { key: 'y', label: 'Full years', children: res.full_years.length ? `${fySpan(res.full_years)} (${res.full_years.length})` : '–' },
                  { key: 'q', label: 'Quarters', children: res.quarters.length ? `${res.quarters[0]} … ${res.quarters[res.quarters.length - 1]} (${res.quarters.length})` : '–' },
                  { key: 'r', label: 'Annual reports', children: res.annual_reports.length ? res.annual_reports.map((fy) => <Tag key={fy}>{fy}</Tag>) : '–' },
                  { key: 'n', label: 'Not downloaded yet', children: newFiles ? <Tag className="tag-down" style={{ margin: 0 }}>{newFiles} new</Tag> : <Tag className="tag-up" style={{ margin: 0 }}>all held</Tag> },
                  { key: 'h', label: 'Held here', children: row.fys.length ? `${fySpan(row.fys)} figures · ${row.xbrl_files} XBRL files${row.annual_reports.length ? ` · reports ${row.annual_reports.join(', ')}` : ''}` : <span className="muted">nothing yet</span> },
                ]}
              />
            </>
          )}

          {row.reports.length > 0 && (
            <ReportsList row={row} job={embedJob} onEmbed={onEmbed} />
          )}

          {storage && (
            <div>
              <div className="eyebrow" style={{ marginBottom: 8 }}>In the S3 bucket{files ? ` · ${files.length} file${files.length === 1 ? '' : 's'}` : ''}</div>
              {files === null ? <Skeleton active paragraph={{ rows: 2 }} title={false} />
                : files.length === 0 ? <span className="muted">No files yet.</span>
                  : (
                    <Table<StoredFile>
                      size="small" rowKey="key" dataSource={files}
                      pagination={files.length > 10 ? { pageSize: 10, size: 'small' } : false}
                      columns={[
                        { title: 'File', dataIndex: 'name', render: (n: string, f) => <span className="file-name"><FileTextOutlined /> <span title={f.key}>{n}</span></span> },
                        { title: 'Type', dataIndex: 'kind', width: 110, render: (k: string) => (k === 'xbrl' ? 'XBRL' : 'Annual report') },
                        { title: 'Size', dataIndex: 'size', width: 80, align: 'right', render: (n: number) => <span className="num muted">{size(n)}</span> },
                        { title: '', key: 'dl', width: 44, render: (_, f) => <Tooltip title="Download"><Button size="small" type="text" icon={<DownloadOutlined />} href={f.url} target="_blank" aria-label={`Download ${f.name}`} /></Tooltip> },
                      ]}
                    />
                  )}
            </div>
          )}

          {res && newFiles > 0 && !active && (
            <div>
              <div className="eyebrow" style={{ marginBottom: 6 }}>Or from the command line</div>
              <Typography.Paragraph copyable={{ text: cmd }} className="cmd">{cmd}</Typography.Paragraph>
            </div>
          )}
          {res && <span className="muted" style={{ fontSize: 12 }}>NSE checked {new Date(res.checked_at).toLocaleString('en-IN')} · kept for an hour</span>}
        </div>
      )}
    </Drawer>
  )
}

const STEP: Record<Job['stage'], number> = { queued: 0, listing: 0, downloading: 1, ingesting: 2, done: 3, failed: 0 }

function DownloadPanel({ row, job, newFiles, checked, storage, onDownload }: {
  row: ListedRow; job?: Job; newFiles: number; checked: boolean; storage: boolean; onDownload: () => void
}) {
  const active = isActive(job)
  if (job && (active || job.stage === 'done' || job.stage === 'failed')) {
    const failedAt = job.stage === 'failed' ? (job.total ? (job.done < job.total ? 1 : 2) : 0) : undefined
    return (
      <Card size="small" className="dl-card">
        <div className="dl-head">
          <b>{job.stage === 'done' ? 'Downloaded' : job.stage === 'failed' ? 'Download failed' : job.stage === 'queued' ? 'Queued' : 'Downloading'}</b>
          <span className="muted">{job.message}</span>
        </div>
        <Progress percent={pct(job)} status={job.stage === 'failed' ? 'exception' : job.stage === 'done' ? 'success' : 'active'} size="small" />
        <Steps
          size="small" type="dot" style={{ marginTop: 10 }}
          current={job.stage === 'failed' ? failedAt : STEP[job.stage]}
          status={job.stage === 'failed' ? 'error' : job.stage === 'done' ? 'finish' : 'process'}
          items={[
            { title: 'List on NSE' },
            { title: 'Download', content: job.total ? `${job.done}/${job.total}` : undefined },
            { title: storage ? 'Save · ingest' : 'Ingest' },
            { title: 'Ready' },
          ]}
        />
        {job.stage === 'done' && (
          <div className="dl-result">
            <CheckCircleFilled style={{ color: 'var(--up)' }} />
            <span>
              {job.facts ? <><b>{job.facts}</b> figures{job.years.length ? ` (${fySpan(job.years)})` : ''}</> : 'No figures'}
              {job.passages ? <> · <b>{job.passages.toLocaleString('en-IN')}</b> report passages</> : null}
              {job.added_company ? ' · added to the company list' : ''}
            </span>
          </div>
        )}
        {job.error && <Alert type="error" showIcon title={job.error} style={{ marginTop: 10 }} />}
        {job.failed_files > 0 && job.stage === 'done' && <Alert type="warning" showIcon style={{ marginTop: 10 }} title={`${job.failed_files} file${job.failed_files === 1 ? '' : 's'} could not be downloaded from NSE`} />}
        {!active && (job.stage === 'failed' || newFiles > 0) && (
          <Button style={{ marginTop: 10 }} icon={<ReloadOutlined />} onClick={onDownload}>Try again</Button>
        )}
      </Card>
    )
  }
  const nothingNew = checked && newFiles === 0 && hasData(row)
  return (
    <Card size="small" className="dl-card">
      <div className="dl-head">
        <b>{hasData(row) ? 'Get the latest filings' : 'No data held for this company yet'}</b>
        <span className="muted">
          {nothingNew ? 'Everything NSE has is already held.'
            : `Downloads every XBRL result and annual report NSE has for the last 5 years${storage ? ', saves each file to S3' : ''}, then ingests the figures and report text${row.tracked ? '' : ' (and adds the company to the tracked list)'}.`}
        </span>
      </div>
      <Button type="primary" icon={<DownloadOutlined />} onClick={onDownload} disabled={nothingNew} style={{ marginTop: 10 }}>
        {checked && newFiles ? `Download ${newFiles} new file${newFiles === 1 ? '' : 's'}${storage ? ' to S3' : ''}` : `Download${storage ? ' to S3' : ''} & ingest`}
      </Button>
    </Card>
  )
}

/** The company's annual-report PDFs: passages, how many are embedded, and Embed per report. */
function ReportsList({ row, job, onEmbed }: { row: ListedRow; job: EmbedJob | null; onEmbed: (docIds: string[]) => void }) {
  const running = embedding(job)
  const live = new Map((job?.items ?? []).map((i) => [i.doc_id, i]))
  const pending = row.reports.filter((r) => r.embedded < r.passages)
  return (
    <div>
      <div className="dl-head" style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
        <div className="eyebrow">Annual reports · semantic search</div>
        {pending.length > 1 && (
          <Button size="small" icon={<ThunderboltOutlined />} disabled={running} onClick={() => onEmbed(pending.map((r) => r.doc_id))}>
            Embed all {pending.length}
          </Button>
        )}
      </div>
      <div className="report-list">
        {row.reports.map((r) => {
          const i = live.get(r.doc_id)
          const done = i && running ? i.done : r.embedded
          const total = i && running ? i.passages : r.passages
          const full = done >= total && total > 0
          return (
            <div key={r.doc_id} className="report-row">
              <FileTextOutlined />
              <span className="report-name"><b>{r.fy ?? '?'}</b> annual report</span>
              <Progress percent={total ? Math.round((done / total) * 100) : 0} size="small" showInfo={false}
                status={i?.state === 'running' && running ? 'active' : full ? 'success' : 'normal'} style={{ margin: 0, flex: 1 }} />
              <span className="num muted" style={{ fontSize: 12, minWidth: 96, textAlign: 'right' }}>{done.toLocaleString('en-IN')}/{total.toLocaleString('en-IN')}</span>
              {full ? <Tag className="tag-up" style={{ margin: 0 }}>embedded</Tag>
                : i && running && i.state === 'waiting' ? <Tag style={{ margin: 0 }}>queued</Tag>
                  : i && running && i.state === 'running' ? <Tag style={{ margin: 0 }}>embedding…</Tag>
                    : <Button size="small" icon={<ThunderboltOutlined />} disabled={running} onClick={() => onEmbed([r.doc_id])}>Embed</Button>}
            </div>
          )
        })}
      </div>
    </div>
  )
}
