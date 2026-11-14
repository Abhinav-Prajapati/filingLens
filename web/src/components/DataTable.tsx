import { useMemo } from 'react'
import { Table as AntTable } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { Table } from '../lib/api'
import { useMeta } from '../lib/meta'
import { seriesColor, useTokens } from '../lib/theme'

type Row = Record<string, unknown> & { _i: number }

/** The table view: every value the chart shows, as filed. Sortable; changes signed and tinted;
 *  a single value column gets an in-cell bar. */
export function DataTable({ table, hideKeys = [] }: { table: Table; hideKeys?: string[] }) {
  const t = useTokens()
  const { metricLabel, companies } = useMeta()
  const cols = table.columns.filter((c) => !hideKeys.includes(c.key))
  const tickers = new Set(companies.map((c) => c.ticker))
  const valueCols = cols.filter((c) => c.type === 'number' || c.type === 'percent')
  const single = valueCols.length === 1 ? valueCols[0] : null
  const max = single ? Math.max(...table.rows.map((r) => Math.abs((r[single.key] as number) ?? 0))) : 0

  const data = useMemo<Row[]>(() => table.rows.map((r, i) => ({ ...r, _i: i })), [table])

  const columns: ColumnsType<Row> = cols.map((c, ci) => {
    const numeric = c.type !== 'period' && c.type !== 'text' && c.key !== 'metric'
    const label = tickers.has(c.key) ? c.label : c.key === 'metric' ? 'Metric' : c.label.includes('_') ? metricLabel(c.label) : c.label
    const unit = c.unit && c.type !== 'change' && c.type !== 'percent' ? (c.unit === 'INR crore' ? '₹ cr' : c.unit) : c.type === 'change' && c.unit === 'pp' ? 'pp' : ''
    return {
      key: c.key,
      dataIndex: c.key,
      align: numeric ? 'right' : 'left',
      title: (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap' }}>
          {tickers.has(c.key) && <span className="swatch" style={{ background: seriesColor(t, c.key, ci) }} />}
          {label}
          {unit && <span style={{ opacity: 0.65, fontWeight: 400 }}>· {unit}</span>}
        </span>
      ),
      sorter: (a, b) => {
        const x = a[c.key], y = b[c.key]
        if (x == null) return 1
        if (y == null) return -1
        return x < y ? -1 : x > y ? 1 : 0
      },
      render: (v: unknown, row) => {
        const disp = table.display[row._i]
        if (v == null) return <span className="muted">–</span>
        let text = disp[c.key] ?? String(v)
        if (c.key === 'metric' && typeof v === 'string') text = metricLabel(v)
        if (c.type === 'period') return <span className="period">{text}</span>
        if (c.type === 'change' && typeof v === 'number') return <span className={v > 0 ? 'pos' : v < 0 ? 'neg' : ''}>{text}</span>
        if (single && c.key === single.key && typeof v === 'number' && max > 0) {
          return <span className="cell-bar"><b style={{ width: Math.max(2, (Math.abs(v) / max) * 64) }} />{text}</span>
        }
        return text
      },
    }
  })

  return (
    <AntTable<Row>
      className="data-table"
      size="small"
      bordered={false}
      columns={columns}
      dataSource={data}
      rowKey="_i"
      pagination={data.length > 12 ? { pageSize: 12, size: 'small', hideOnSinglePage: true } : false}
      scroll={{ x: 'max-content' }}
      showSorterTooltip={false}
    />
  )
}
