import type { ReactNode } from 'react'
import { Avatar, Flex, Segmented, Typography, theme } from 'antd'
import { DesktopOutlined, FundProjectionScreenOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons'
import { setThemePref, useThemePref, type ThemePref } from '../lib/theme'

/** The product mark: the primary colour is the brand colour, used here and for actions only. */
export function Brand({ sub = 'NSE filings, verified' }: { sub?: string | null }) {
  const { token } = theme.useToken()
  return (
    <Flex align="center" gap="small">
      <Avatar shape="square" size={32} icon={<FundProjectionScreenOutlined />} style={{ background: token.colorPrimary }} />
      <Flex vertical>
        <Typography.Text strong style={{ fontSize: token.fontSizeLG }}>filingLens</Typography.Text>
        {sub && <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM }}>{sub}</Typography.Text>}
      </Flex>
    </Flex>
  )
}

export function ThemeSwitch({ block }: { block?: boolean }) {
  const pref = useThemePref()
  return (
    <Segmented<ThemePref>
      block={block}
      value={pref}
      onChange={setThemePref}
      options={[
        { value: 'dark', icon: <MoonOutlined />, tooltip: 'Dark' },
        { value: 'light', icon: <SunOutlined />, tooltip: 'Light' },
        { value: 'system', icon: <DesktopOutlined />, tooltip: 'Follow the system' },
      ]}
      aria-label="Theme"
    />
  )
}

/** The page title block every page starts with: title, one line of context, actions. */
export function PageHeader({ title, description, extra }: { title: ReactNode; description?: ReactNode; extra?: ReactNode }) {
  const { token } = theme.useToken()
  return (
    <Flex justify="space-between" align="flex-end" gap="middle" wrap style={{ marginBottom: token.marginLG }}>
      <Flex vertical gap={token.marginXXS}>
        <Typography.Title level={4} style={{ margin: 0 }}>{title}</Typography.Title>
        {description && <Typography.Text type="secondary">{description}</Typography.Text>}
      </Flex>
      {extra}
    </Flex>
  )
}
