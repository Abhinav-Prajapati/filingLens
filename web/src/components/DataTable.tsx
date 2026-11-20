import { useMemo } from 'react'
import { Badge, Flex, Progress, Table as AntTable, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { Table } from '../lib/api'
import { useMeta } from '../lib/meta'
import { seriesColor, useTokens } from '../lib/theme'

type Row = Record<string, unknown> & { _i: number }

/** Every value the chart shows, as filed. Sortable; changes signed and coloured by the success
 *  and error tokens; a single value column gets an in-cell bar. */
export function DataTable({ table, hideKeys = [] }: { table: Table; hideKeys?: string[] }) {
  const t = useTokens()
  const { metricLabel, companies } = useMeta()
  const cols = table.columns.filter((c) => !hideKeys.includes(c.key))
  const tickers = new Set(companies.map((c) => c.ticker))
  const coCols = cols.map((c) => c.key).filter((k) => tickers.has(k))
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
        <Flex gap="small" align="center" justify={numeric ? 'flex-end' : 'flex-start'} style={{ whiteSpace: 'nowrap' }}>
          {tickers.has(c.key) ? <Badge color={seriesColor(t, c.key, ci, coCols)} text={label} /> : label}
          {unit && <Typography.Text type="secondary">{unit}</Typography.Text>}
        </Flex>
      ),
      sorter: (a, b) => {
        const x = a[c.key], y = b[c.key]
        if (x == null) return 1
        if (y == null) return -1
        return x < y ? -1 : x > y ? 1 : 0
      },
      render: (v: unknown, row) => {
        const disp = table.display[row._i]
        if (v == null) return <Typography.Text type="secondary">–</Typography.Text>
        let text = disp[c.key] ?? String(v)
        if (c.key === 'metric' && typeof v === 'string') text = metricLabel(v)
        if (c.type === 'period') return <Typography.Text strong>{text}</Typography.Text>
        if (c.type === 'change' && typeof v === 'number') {
          return <Typography.Text type={v > 0 ? 'success' : v < 0 ? 'danger' : 'secondary'}>{text}</Typography.Text>
        }
        if (single && c.key === single.key && typeof v === 'number' && max > 0) {
          return (
            <Flex gap="small" align="center" justify="flex-end">
              <Progress percent={(Math.abs(v) / max) * 100} showInfo={false} size="small" style={{ width: 64, margin: 0 }} />
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>{text}</span>
            </Flex>
          )
        }
        return <span style={{ fontVariantNumeric: 'tabular-nums' }}>{text}</span>
      },
    }
  })

  return (
    <AntTable<Row>
      size="small"
      columns={columns}
      dataSource={data}
      rowKey="_i"
      pagination={data.length > 12 ? { pageSize: 12, size: 'small', hideOnSinglePage: true } : false}
      scroll={{ x: 'max-content' }}
      showSorterTooltip={false}
    />
  )
}
