import { useState } from 'react'
import { Button, Card, Collapse, Tag } from 'antd'
import { FileTextOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import type { Passage, Source } from '../lib/api'
import { useMeta } from '../lib/meta'

/** Where each number came from: document, page, XBRL tag, and whether it was matched on the page. */
export function Sources({ sources, notes }: { sources: Source[]; notes: string[] }) {
  const { metricLabel } = useMeta()
  if (!sources.length && !notes.length) return null
  const docs = new Set(sources.map((s) => s.document ?? s.label))
  const verified = sources.filter((s) => s.verified === 'pdf').length
  const label = (
    <span style={{ display: 'inline-flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--ink-2)' }}>
      <FileTextOutlined />
      <span style={{ whiteSpace: 'nowrap' }}>{sources.length ? `${sources.length} source${sources.length > 1 ? 's' : ''} · ${docs.size} document${docs.size > 1 ? 's' : ''}` : 'Notes'}</span>
      {verified > 0 && <Tag className="tag-up" icon={<SafetyCertificateOutlined />} style={{ margin: 0 }}>{verified} matched on the report page</Tag>}
    </span>
  )
  return (
    <Collapse
      ghost
      size="small"
      style={{ borderTop: '1px solid var(--rule)', borderRadius: 0 }}
      items={[{
        key: 'src',
        label,
        children: (
          <>
            {sources.map((s, i) => (
              <div key={i} className="src-row">
                <span className="src-n">{String(i + 1).padStart(2, '0')}</span>
                <span>
                  <span style={{ fontWeight: 500 }}>{s.document ?? 'XBRL filing'}{s.page ? `, p.${s.page}` : ''}</span>
                  <br />
                  <span className="src-detail">
                    {[s.ticker, s.period, s.metric ? metricLabel(s.metric) : s.section, s.xbrl_tag].filter(Boolean).join(' · ')}
                  </span>
                </span>
                {s.verified === 'pdf'
                  ? <Tag className="tag-up" icon={<SafetyCertificateOutlined />} style={{ margin: 0 }}>on page</Tag>
                  : <Tag style={{ margin: 0 }}>{s.kind}</Tag>}
              </div>
            ))}
            {notes.length > 0 && (
              <ul style={{ margin: '10px 0 0', paddingLeft: 18, color: 'var(--muted)', fontSize: 12.5 }}>
                {notes.map((n, i) => <li key={i}>{n}</li>)}
              </ul>
            )}
          </>
        ),
      }]}
    />
  )
}

const SECTION: Record<string, string> = {
  mdna: 'MD&A', front_matter: 'Front matter', financial_statements: 'Financial statements', directors_report: "Directors' report", financials: 'Financial statements',
  notes: 'Notes', governance: 'Corporate governance', brsr: 'BRSR',
}

/** Report passages for text questions; query words highlighted. */
export function Passages({ passages, query }: { passages: Passage[]; query: string }) {
  const words = query.toLowerCase().split(/\W+/).filter((w) => w.length > 3)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {passages.map((p) => <PassageCard key={p.rank} p={p} words={words} />)}
    </div>
  )
}

function PassageCard({ p, words }: { p: Passage; words: string[] }) {
  const [open, setOpen] = useState(p.rank === 1)
  const doc = p.source.split('|').slice(0, 2).map((s) => s.trim()).join(' · ')
  // PDF text breaks every line; join lines that continue a sentence
  const flowed = p.text.replace(/([^.:\n])\n(?=[a-z(“"])/g, '$1 ')
  const text = open ? flowed : flowed.slice(0, 280) + (flowed.length > 280 ? '…' : '')
  const re = words.length ? new RegExp(`\\b(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})\\w*`, 'gi') : null
  const parts = re ? text.split(re) : [text]
  return (
    <Card size="small" className="passage" styles={{ body: { padding: '12px 16px' } }}>
      <div className="passage-head">
        <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
          <Tag style={{ margin: 0 }} className="mono">#{p.rank}</Tag>
          <b style={{ fontWeight: 600 }}>{doc}</b>
          {p.page ? <span className="muted">p.{p.page}</span> : null}
        </span>
        {p.section && <Tag variant="outlined" style={{ margin: 0 }}>{SECTION[p.section] ?? p.section}</Tag>}
      </div>
      <p>{parts.map((part, i) => (re && words.some((w) => part.toLowerCase().startsWith(w)) ? <mark key={i}>{part}</mark> : part))}</p>
      {p.text.length > 280 && (
        <Button type="link" size="small" style={{ paddingInline: 0, marginTop: 4 }} onClick={() => setOpen((o) => !o)}>
          {open ? 'Show less' : 'Show full passage'}
        </Button>
      )}
    </Card>
  )
}
