import { Badge, Space, Typography } from 'antd'
import { CaretDownFilled, CaretUpFilled, MinusOutlined } from '@ant-design/icons'

/** Series identities as Badge colour dots; nothing for a single series (the title names it). */
export function Legend({ items }: { items: { label: string; color: string }[] }) {
  if (items.length < 2) return null
  return (
    <Space size="middle" wrap aria-label="Legend">
      {items.map((i) => <Badge key={i.label} color={i.color} text={i.label} />)}
    </Space>
  )
}

/** A signed change, coloured by whether it is good for this metric (success / danger). */
export function Delta({ value, unit = '%', goodWhenUp = true }: { value: number | null | undefined; unit?: '%' | 'pp'; goodWhenUp?: boolean }) {
  if (value == null) return null
  const flat = Math.abs(value) < 0.005
  const good = (value > 0) === goodWhenUp
  const arrow = flat ? <MinusOutlined /> : value > 0 ? <CaretUpFilled /> : <CaretDownFilled />
  return (
    <Typography.Text type={flat ? 'secondary' : good ? 'success' : 'danger'} style={{ fontVariantNumeric: 'tabular-nums' }}>
      {arrow} {value > 0 ? '+' : value < 0 ? '−' : ''}{Math.abs(value).toFixed(unit === 'pp' ? 2 : 1)}{unit === 'pp' ? ' pp' : '%'}
    </Typography.Text>
  )
}
