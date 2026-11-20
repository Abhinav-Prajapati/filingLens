import { Fragment, type ReactNode } from 'react'
import { Divider, Flex, theme } from 'antd'

/** Items one per row, separated by Dividers (Ant Design's List is deprecated in v6). */
export function Rows<T>({ items, render, rowKey, maxHeight }: {
  items: T[]; render: (item: T, index: number) => ReactNode; rowKey: (item: T, index: number) => string; maxHeight?: number
}) {
  const { token } = theme.useToken()
  return (
    <Flex vertical style={maxHeight ? { maxHeight, overflow: 'auto' } : undefined}>
      {items.map((item, i) => (
        <Fragment key={rowKey(item, i)}>
          {i > 0 && <Divider style={{ marginBlock: token.marginXS }} />}
          {render(item, i)}
        </Fragment>
      ))}
    </Flex>
  )
}
