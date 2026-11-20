import { Card, Collapse, Flex, Tag, Typography } from 'antd'
import { FileTextOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import type { Passage, Source } from '../lib/api'
import { useMeta } from '../lib/meta'
import { Rows } from './Rows'

/** Where each number came from: document, page, XBRL tag, and whether it was matched on the page. */
export function Sources({ sources, notes }: { sources: Source[]; notes: string[] }) {
  const { metricLabel } = useMeta()
  if (!sources.length && !notes.length) return null
  const docs = new Set(sources.map((s) => s.document ?? s.label))
  const verified = sources.filter((s) => s.verified === 'pdf').length
  return (
    <Collapse
      size="small"
      items={[{
        key: 'src',
        label: (
          <Flex gap="small" align="center" wrap>
            <FileTextOutlined />
            <Typography.Text>
              {sources.length ? `${sources.length} source${sources.length > 1 ? 's' : ''} · ${docs.size} document${docs.size > 1 ? 's' : ''}` : 'Notes'}
            </Typography.Text>
            {verified > 0 && <Tag color="success" icon={<SafetyCertificateOutlined />} style={{ margin: 0 }}>{verified} matched on the report page</Tag>}
          </Flex>
        ),
        children: (
          <Flex vertical gap="small">
            {sources.length > 0 && (
              <Rows
                items={sources}
                rowKey={(_, i) => String(i)}
                render={(s, i) => (
                  <Flex justify="space-between" align="flex-start" gap="middle">
                    <Flex vertical>
                      <Typography.Text>{`${String(i + 1).padStart(2, '0')} · ${s.document ?? 'XBRL filing'}${s.page ? `, p.${s.page}` : ''}`}</Typography.Text>
                      <Typography.Text type="secondary">{[s.ticker, s.period, s.metric ? metricLabel(s.metric) : s.section, s.xbrl_tag].filter(Boolean).join(' · ')}</Typography.Text>
                    </Flex>
                    {s.verified === 'pdf'
                      ? <Tag color="success" icon={<SafetyCertificateOutlined />} style={{ margin: 0 }}>On page</Tag>
                      : <Tag style={{ margin: 0 }}>{s.kind}</Tag>}
                  </Flex>
                )}
              />
            )}
            {notes.length > 0 && (
              <Flex vertical gap={4}>
                {notes.map((n, i) => <Typography.Text key={i} type="secondary">{n}</Typography.Text>)}
              </Flex>
            )}
          </Flex>
        ),
      }]}
    />
  )
}

const SECTION: Record<string, string> = {
  mdna: 'MD&A', front_matter: 'Front matter', financial_statements: 'Financial statements', directors_report: "Directors' report", financials: 'Financial statements',
  notes: 'Notes', governance: 'Corporate governance', brsr: 'BRSR', corporate_governance: 'Corporate governance',
  auditor_report: "Auditor's report", notice: 'Notice', sustainability: 'Sustainability',
}

/** Report passages for text questions; query words highlighted. */
export function Passages({ passages, query }: { passages: Passage[]; query: string }) {
  const words = query.toLowerCase().split(/\W+/).filter((w) => w.length > 3)
  return (
    <Flex vertical gap="middle">
      {passages.map((p) => <PassageCard key={p.rank} p={p} words={words} />)}
    </Flex>
  )
}

function PassageCard({ p, words }: { p: Passage; words: string[] }) {
  const doc = p.source.split('|').slice(0, 2).map((s) => s.trim()).join(' · ')
  // PDF text breaks every line; join lines that continue a sentence
  const text = p.text.replace(/([^.:\n])\n(?=[a-z(“"])/g, '$1 ')
  const re = words.length ? new RegExp(`\\b(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})\\w*`, 'gi') : null
  const parts = re ? text.split(re) : [text]
  return (
    <Card
      size="small"
      type="inner"
      title={
        <Flex gap="small" align="center" wrap>
          <Tag style={{ margin: 0 }}>#{p.rank}</Tag>
          <Typography.Text strong>{doc}</Typography.Text>
          {p.page ? <Typography.Text type="secondary">p.{p.page}</Typography.Text> : null}
        </Flex>
      }
      extra={p.section ? <Tag color="blue" style={{ margin: 0 }}>{SECTION[p.section] ?? p.section}</Tag> : null}
    >
      <Typography.Paragraph
        style={{ margin: 0, whiteSpace: 'pre-line' }}
        ellipsis={{ rows: p.rank === 1 ? 8 : 4, expandable: 'collapsible', symbol: (open) => (open ? 'Show less' : 'Show full passage') }}
      >
        {parts.map((part, i) => (re && words.some((w) => part.toLowerCase().startsWith(w))
          ? <Typography.Text key={i} mark>{part}</Typography.Text> : part))}
      </Typography.Paragraph>
    </Card>
  )
}
