import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, App as AntApp, Button, Card, Col, Descriptions, Divider, Drawer, Empty, Flex, Input, Progress, Row, Segmented, Select,
  Skeleton, Space, Statistic, Steps, Table, Tag, Tooltip, Typography, theme,
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
import { PageHeader } from './Chrome'
import { Rows } from './Rows'

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
  const { token } = theme.useToken()
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

  const page = (children: React.ReactNode) => (
    <div style={{ maxWidth: 1400, margin: '0 auto' }}>
      <PageHeader title="Data" description="Every NSE-listed company: what is held here, what NSE has, and what is embedded for search" />
      {children}
    </div>
  )
  if (error && !data) return page(<Alert type="error" showIcon title="Couldn’t load the company list" description={error} />)
  if (!data) return page(<Card><Skeleton active paragraph={{ rows: 12 }} /></Card>)
  if (!data.total) {
    return page(
      <Card>
        <Empty description={<>NSE’s equity list hasn’t been downloaded yet. Run <Typography.Text code>filinglens companies --refresh</Typography.Text>, then reload.</>} />
      </Card>,
    )
  }

  const secondary = (text: React.ReactNode) => <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM }}>{text}</Typography.Text>
  const pendingNote = (n: number) => <Typography.Text type="warning" style={{ fontSize: token.fontSizeSM }}>{n} downloaded, not ingested</Typography.Text>

  const columns: ColumnsType<ListedRow> = [
    {
      title: 'Company', key: 'symbol', fixed: 'left', width: 220,
      sorter: (a, b) => a.symbol.localeCompare(b.symbol), defaultSortOrder: 'ascend',
      render: (_, r) => (
        <Flex vertical>
          <Typography.Text strong>{r.symbol}</Typography.Text>
          <Typography.Text type="secondary" ellipsis={{ tooltip: r.name }} style={{ maxWidth: 196 }}>{r.name}</Typography.Text>
        </Flex>
      ),
    },
    {
      title: 'Series · listed', dataIndex: 'listed_on', width: 120,
      sorter: (a, b) => (a.listed_on ?? '').localeCompare(b.listed_on ?? ''),
      render: (d: string | null, r) => (
        <Flex vertical gap={4} align="flex-start">
          <Tag style={{ margin: 0 }}>{r.series}</Tag>
          {secondary(date(d))}
        </Flex>
      ),
    },
    {
      title: 'Status', key: 'tracked', width: 104,
      sorter: (a, b) => Number(b.tracked) - Number(a.tracked),
      render: (_, r) => (r.tracked ? <Tag color="blue" style={{ margin: 0 }}>Tracked</Tag> : secondary('Not tracked')),
    },
    {
      title: <Tooltip title="Ingested XBRL results: the years with figures, the latest quarter, and files downloaded but not ingested yet">XBRL figures</Tooltip>,
      key: 'xbrl', width: 170,
      sorter: (a, b) => a.fys.length - b.fys.length || a.xbrl_files - b.xbrl_files,
      render: (_, r) => (
        <Flex vertical>
          {r.fys.length || r.xbrl_files
            ? <Typography.Text><Typography.Text strong>{fySpan(r.fys) || 'Quarters only'}</Typography.Text> {secondary(`· ${r.xbrl_files} file${r.xbrl_files === 1 ? '' : 's'}`)}</Typography.Text>
            : <Typography.Text type="secondary">–</Typography.Text>}
          {r.latest_quarter && secondary(`Latest ${r.latest_quarter}`)}
          {r.pending_xbrl > 0 && pendingNote(r.pending_xbrl)}
        </Flex>
      ),
    },
    {
      title: <Tooltip title="Ingested annual-report PDFs. ✓ = embedded for semantic search; a % = partly; plain = keyword search only">Annual reports</Tooltip>,
      key: 'reports', width: 180,
      sorter: (a, b) => a.annual_reports.length - b.annual_reports.length,
      render: (_, r) => (
        <Flex vertical gap={4}>
          {r.reports.length
            ? <Flex gap={4} wrap>{r.reports.map((x) => <ReportTag key={x.doc_id} r={x} />)}</Flex>
            : r.annual_reports.length
              ? <Flex gap={4} wrap>{r.annual_reports.map((fy) => <Tag key={fy} style={{ margin: 0 }}>{fy}</Tag>)}</Flex>
              : <Typography.Text type="secondary">–</Typography.Text>}
          {r.pending_reports > 0 && pendingNote(r.pending_reports)}
        </Flex>
      ),
    },
    {
      title: <Tooltip title="Files kept in the S3 bucket">In S3</Tooltip>, dataIndex: 'stored', width: 80, align: 'right',
      sorter: (a, b) => a.stored - b.stored,
      render: (n: number) => (n ? n : <Typography.Text type="secondary">–</Typography.Text>),
    },
    {
      title: 'Passages', dataIndex: 'passages', width: 100, align: 'right',
      sorter: (a, b) => a.passages - b.passages,
      render: (n: number) => (n ? n.toLocaleString('en-IN') : <Typography.Text type="secondary">–</Typography.Text>),
    },
    {
      title: '', key: 'actions', width: 150, fixed: 'right',
      render: (_, r) => {
        const j = jobs[r.symbol]
        if (isActive(j)) {
          return (
            <Tooltip title="Show progress">
              <Flex vertical role="button" tabIndex={0} onClick={() => setOpen(r)} style={{ cursor: 'pointer' }}
                onKeyDown={(e) => { if (e.key === 'Enter') setOpen(r) }}>
                <Progress percent={pct(j!)} size="small" showInfo={false} status="active" />
                {secondary(j!.stage === 'queued' ? 'Queued' : j!.stage === 'downloading' ? `${j!.done}/${j!.total} files` : j!.stage === 'ingesting' ? 'Ingesting' : 'Asking NSE')}
              </Flex>
            </Tooltip>
          )
        }
        return (
          <Space size={4} style={{ width: '100%', justifyContent: 'flex-end' }}>
            {!hasData(r) && (
              <Tooltip title="Download every filing NSE has, save it to S3 and ingest it">
                <Button size="small" icon={<DownloadOutlined />} onClick={() => download(r)}>Download</Button>
              </Tooltip>
            )}
            {needsEmbedding(r) && (
              <Tooltip title={embedRunning ? 'An embedding job is running' : 'Embed this company’s annual reports for semantic search'}>
                <Button size="small" icon={<ThunderboltOutlined />} disabled={embedRunning} onClick={() => embed({ tickers: [r.symbol] }, r.symbol)} aria-label={`Embed ${r.symbol}'s annual reports`} />
              </Tooltip>
            )}
            <Tooltip title={hasData(r) ? 'NSE, reports and files' : 'Details'}>
              <Button size="small" icon={<CloudServerOutlined />} onClick={() => setOpen(r)} aria-label={`Open ${r.symbol}`} />
            </Tooltip>
          </Space>
        )
      },
    },
  ]

  const tiles = [
    { icon: <DatabaseOutlined />, label: 'Listed on NSE', value: data.total, foot: data.list_updated ? `List from ${date(data.list_updated)}` : '' },
    { icon: <SearchOutlined />, label: 'Tracked', value: counts.tracked, foot: 'In the company list, can be asked about' },
    { icon: <CloudDownloadOutlined />, label: 'With XBRL figures', value: counts.xbrl, foot: 'Results ingested' },
    { icon: <CloudUploadOutlined />, label: 'In S3', value: counts.stored, foot: data.storage ? 'Companies with files in the bucket' : 'No bucket configured' },
  ]

  return page(
    <Flex vertical gap="middle">
      <Row gutter={[16, 16]}>
        {tiles.map((t) => (
          <Col key={t.label} xs={12} lg={6}>
            <Card style={{ height: '100%' }}>
              <Statistic title={<Space size="small">{t.icon}{t.label}</Space>} value={t.value} />
              {secondary(t.foot)}
            </Card>
          </Col>
        ))}
      </Row>

      {!data.storage && <Alert type="warning" showIcon title="No S3 bucket configured" description="Downloads are kept in the local inbox only. Set FILINGLENS_S3_* in .env and start the stack with docker compose." />}
      {running.length > 0 && (
        <Alert
          type="info" showIcon
          title={`Downloading ${running.map((j) => j.symbol).join(', ')}`}
          description="Runs one company at a time (NSE limits request rates). You can keep working; the list updates when each finishes."
        />
      )}

      <EmbeddingPanel index={data.index} job={embedJob.job} device={device} setDevice={setDevice}
        onEmbedAll={() => embed({}, '')} onStop={() => { embedJob.stop().catch((e: Error) => message.error(e.message)) }} />

      <Card size="small">
        <Flex gap="middle" align="center" wrap>
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
            options={SHOW.map((s) => ({ value: s.value, label: <Space size={4}>{s.label}<Typography.Text type="secondary">{counts[s.value].toLocaleString('en-IN')}</Typography.Text></Space> }))}
          />
          <Select value={series} onChange={setSeries} options={seriesOptions} style={{ width: 140 }} aria-label="NSE series" />
          <Typography.Text type="secondary" style={{ marginLeft: 'auto' }}>{rows.length.toLocaleString('en-IN')} shown</Typography.Text>
        </Flex>
      </Card>

      <Card styles={{ body: { padding: 0 } }}>
        <Table<ListedRow>
          size="small"
          rowKey="symbol"
          columns={columns}
          dataSource={rows}
          scroll={{ x: 1140 }}
          showSorterTooltip={false}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={query ? `No listed company matches “${query}”` : 'Nothing here'} /> }}
          pagination={{ defaultPageSize: 25, pageSizeOptions: [25, 50, 100], showSizeChanger: true, size: 'small', showTotal: (n, [a, b]) => `${a}–${b} of ${n.toLocaleString('en-IN')}` }}
          onRow={(r) => ({ onDoubleClick: () => setOpen(r) })}
        />
      </Card>

      <CompanyDrawer row={openRow} job={openRow ? jobs[openRow.symbol] : undefined} storage={data.storage}
        onDownload={download} onClose={() => setOpen(null)}
        embedJob={embedJob.job} onEmbed={(docIds) => openRow && embed({ doc_ids: docIds }, openRow.symbol)} />
    </Flex>,
  )
}

/** One company: what NSE has (live), the download job, and its files in the bucket. */
function CompanyDrawer({ row, job, storage, onDownload, onClose, embedJob, onEmbed }: {
  row: ListedRow | null; job?: Job; storage: boolean; onDownload: (r: ListedRow) => void; onClose: () => void
  embedJob: EmbedJob | null; onEmbed: (docIds: string[]) => void
}) {
  const { token } = theme.useToken()
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
  const section = (label: React.ReactNode) => <Divider titlePlacement="start" plain style={{ margin: 0 }}>{label}</Divider>

  return (
    <Drawer
      open={!!row}
      onClose={onClose}
      size="large"
      title={row && (
        <Flex vertical>
          <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM, fontWeight: 400 }}>{row.tracked ? 'Tracked' : 'Not tracked'} · {row.series} · {row.isin}</Typography.Text>
          <Typography.Text strong style={{ fontSize: token.fontSizeLG }}>{row.name} <Typography.Text type="secondary">{row.symbol}</Typography.Text></Typography.Text>
        </Flex>
      )}
      extra={<Button icon={<ReloadOutlined />} onClick={() => check(sym, true)} disabled={busy || active}>Check NSE again</Button>}
    >
      {row && (
        <Flex vertical gap="large">
          <DownloadPanel row={row} job={job} newFiles={newFiles} checked={!!res} storage={storage} onDownload={() => onDownload(row)} />

          {section('On NSE')}
          {busy && (
            <Flex vertical gap="middle">
              <Steps
                orientation="vertical" size="small" current={1}
                items={[{ title: 'Connect to NSE' }, { title: 'List XBRL results (annual and quarterly)' }, { title: 'List annual reports' }]}
              />
              <Typography.Text type="secondary">NSE is asked slowly on purpose, so this takes about 10 seconds{active ? ' (longer while a download is running)' : ''}.</Typography.Text>
            </Flex>
          )}
          {error && <Alert type="error" showIcon title="NSE check failed" description={error} action={<Button size="small" onClick={() => check(sym, true)}>Retry</Button>} />}
          {res && (
            <Flex vertical gap="small">
              {res.warning && <Alert type="warning" showIcon title={res.warning} />}
              <Descriptions
                size="small" column={1} bordered
                items={[
                  { key: 'x', label: 'XBRL results on NSE', children: res.xbrl_filings ? <Typography.Text strong>{res.xbrl_filings} filings</Typography.Text> : <Typography.Text type="secondary">None in the last 5 years</Typography.Text> },
                  { key: 'y', label: 'Full years', children: res.full_years.length ? `${fySpan(res.full_years)} (${res.full_years.length})` : '–' },
                  { key: 'q', label: 'Quarters', children: res.quarters.length ? `${res.quarters[0]} … ${res.quarters[res.quarters.length - 1]} (${res.quarters.length})` : '–' },
                  { key: 'r', label: 'Annual reports', children: res.annual_reports.length ? <Flex gap={4} wrap>{res.annual_reports.map((fy) => <Tag key={fy} style={{ margin: 0 }}>{fy}</Tag>)}</Flex> : '–' },
                  { key: 'n', label: 'Not downloaded yet', children: newFiles ? <Tag color="warning" style={{ margin: 0 }}>{newFiles} new</Tag> : <Tag color="success" style={{ margin: 0 }}>All held</Tag> },
                  { key: 'h', label: 'Held here', children: row.fys.length ? `${fySpan(row.fys)} figures · ${row.xbrl_files} XBRL files${row.annual_reports.length ? ` · reports ${row.annual_reports.join(', ')}` : ''}` : <Typography.Text type="secondary">Nothing yet</Typography.Text> },
                ]}
              />
              <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM }}>Checked {new Date(res.checked_at).toLocaleString('en-IN')} · kept for an hour</Typography.Text>
            </Flex>
          )}

          {row.reports.length > 0 && <ReportsList row={row} job={embedJob} onEmbed={onEmbed} />}

          {storage && (
            <>
              {section(`In the S3 bucket${files ? ` · ${files.length} file${files.length === 1 ? '' : 's'}` : ''}`)}
              {files === null ? <Skeleton active paragraph={{ rows: 2 }} title={false} />
                : files.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No files yet" />
                  : (
                    <Table<StoredFile>
                      size="small" rowKey="key" dataSource={files}
                      pagination={files.length > 10 ? { pageSize: 10, size: 'small' } : false}
                      columns={[
                        { title: 'File', dataIndex: 'name', render: (n: string, f) => <Space size="small"><FileTextOutlined /><Typography.Text ellipsis={{ tooltip: f.key }} style={{ maxWidth: 260 }}>{n}</Typography.Text></Space> },
                        { title: 'Type', dataIndex: 'kind', width: 120, render: (k: string) => (k === 'xbrl' ? 'XBRL' : 'Annual report') },
                        { title: 'Size', dataIndex: 'size', width: 88, align: 'right', render: (n: number) => <Typography.Text type="secondary">{size(n)}</Typography.Text> },
                        { title: '', key: 'dl', width: 48, render: (_, f) => <Tooltip title="Download"><Button size="small" type="text" icon={<DownloadOutlined />} href={f.url} target="_blank" aria-label={`Download ${f.name}`} /></Tooltip> },
                      ]}
                    />
                  )}
            </>
          )}

          {res && newFiles > 0 && !active && (
            <>
              {section('Or from the command line')}
              <Typography.Text code copyable={{ text: cmd }}>{cmd}</Typography.Text>
            </>
          )}
        </Flex>
      )}
    </Drawer>
  )
}

const STEP: Record<Job['stage'], number> = { queued: 0, listing: 0, downloading: 1, ingesting: 2, done: 3, failed: 0 }

function DownloadPanel({ row, job, newFiles, checked, storage, onDownload }: {
  row: ListedRow; job?: Job; newFiles: number; checked: boolean; storage: boolean; onDownload: () => void
}) {
  const { token } = theme.useToken()
  const active = isActive(job)
  if (job && (active || job.stage === 'done' || job.stage === 'failed')) {
    const failedAt = job.stage === 'failed' ? (job.total ? (job.done < job.total ? 1 : 2) : 0) : undefined
    const status = job.stage === 'done' ? 'Downloaded' : job.stage === 'failed' ? 'Download failed' : job.stage === 'queued' ? 'Queued' : 'Downloading'
    return (
      <Card size="small" title={status} extra={!active && (job.stage === 'failed' || newFiles > 0) && <Button size="small" icon={<ReloadOutlined />} onClick={onDownload}>Try again</Button>}>
        <Flex vertical gap="middle">
          <Typography.Text type="secondary">{job.message}</Typography.Text>
          <Progress percent={pct(job)} status={job.stage === 'failed' ? 'exception' : job.stage === 'done' ? 'success' : 'active'} size="small" />
          <Steps
            size="small"
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
            <Space size="small">
              <CheckCircleFilled style={{ color: token.colorSuccess }} />
              <Typography.Text>
                {job.facts ? <><Typography.Text strong>{job.facts}</Typography.Text> figures{job.years.length ? ` (${fySpan(job.years)})` : ''}</> : 'No figures'}
                {job.passages ? <> · <Typography.Text strong>{job.passages.toLocaleString('en-IN')}</Typography.Text> report passages</> : null}
                {job.added_company ? ' · added to the company list' : ''}
              </Typography.Text>
            </Space>
          )}
          {job.error && <Alert type="error" showIcon title={job.error} />}
          {job.failed_files > 0 && job.stage === 'done' && <Alert type="warning" showIcon title={`${job.failed_files} file${job.failed_files === 1 ? '' : 's'} could not be downloaded from NSE`} />}
        </Flex>
      </Card>
    )
  }
  const nothingNew = checked && newFiles === 0 && hasData(row)
  return (
    <Card size="small" title={hasData(row) ? 'Get the latest filings' : 'No data held for this company yet'}>
      <Flex vertical gap="middle" align="flex-start">
        <Typography.Text type="secondary">
          {nothingNew ? 'Everything NSE has is already held.'
            : `Downloads every XBRL result and annual report NSE has for the last 5 years${storage ? ', saves each file to S3' : ''}, then ingests the figures and report text${row.tracked ? '' : ' (and adds the company to the tracked list)'}.`}
        </Typography.Text>
        <Button type="primary" icon={<DownloadOutlined />} onClick={onDownload} disabled={nothingNew}>
          {checked && newFiles ? `Download ${newFiles} new file${newFiles === 1 ? '' : 's'}${storage ? ' to S3' : ''}` : `Download${storage ? ' to S3' : ''} and ingest`}
        </Button>
      </Flex>
    </Card>
  )
}

/** The company's annual-report PDFs: passages, how many are embedded, and Embed per report. */
function ReportsList({ row, job, onEmbed }: { row: ListedRow; job: EmbedJob | null; onEmbed: (docIds: string[]) => void }) {
  const running = embedding(job)
  const live = new Map((job?.items ?? []).map((i) => [i.doc_id, i]))
  const pending = row.reports.filter((r) => r.embedded < r.passages)
  return (
    <Card
      size="small"
      title="Annual reports · semantic search"
      extra={pending.length > 1 && (
        <Button size="small" icon={<ThunderboltOutlined />} disabled={running} onClick={() => onEmbed(pending.map((r) => r.doc_id))}>
          Embed all {pending.length}
        </Button>
      )}
    >
      <Rows
        items={row.reports}
        rowKey={(r) => r.doc_id}
        render={(r) => {
          const i = live.get(r.doc_id)
          const done = i && running ? i.done : r.embedded
          const total = i && running ? i.passages : r.passages
          const full = done >= total && total > 0
          return (
            <Flex align="center" gap="middle" style={{ width: '100%' }}>
              <Space size="small" style={{ minWidth: 140 }}><FileTextOutlined /><Typography.Text strong>{r.fy ?? '?'}</Typography.Text><Typography.Text>annual report</Typography.Text></Space>
              <Progress percent={total ? Math.round((done / total) * 100) : 0} size="small" showInfo={false}
                status={i?.state === 'running' && running ? 'active' : full ? 'success' : 'normal'} style={{ margin: 0, flex: 1 }} />
              <Typography.Text type="secondary" style={{ minWidth: 96, textAlign: 'right' }}>{done.toLocaleString('en-IN')}/{total.toLocaleString('en-IN')}</Typography.Text>
              {full ? <Tag color="success" style={{ margin: 0 }}>Embedded</Tag>
                : i && running && i.state === 'waiting' ? <Tag style={{ margin: 0 }}>Queued</Tag>
                  : i && running && i.state === 'running' ? <Tag color="processing" style={{ margin: 0 }}>Embedding</Tag>
                    : <Button size="small" icon={<ThunderboltOutlined />} disabled={running} onClick={() => onEmbed([r.doc_id])}>Embed</Button>}
            </Flex>
          )
        }}
      />
    </Card>
  )
}
