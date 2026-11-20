import type { CSSProperties, ReactNode } from 'react'
import { Avatar, Flex, Segmented, Typography, theme } from 'antd'
import Icon, { DesktopOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons'
import { setThemePref, useThemePref, type ThemePref } from '../lib/theme'

/** The product mark: the primary colour is the brand colour, used here and for actions only. */
/** A magnifying lens with a rising trend line inside. Drawn in currentColor on a 24-unit grid. */
const LensSvg = () => (
  <svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <circle cx="10" cy="10" r="7" strokeWidth="2" />
    <path d="M15.2 15.2 21 21" strokeWidth="2.6" />
    <path d="M6.3 12.6 8.9 10l1.9 1.6 3-3.4" strokeWidth="1.6" />
    <path d="M11.9 8.2h1.9v1.9" strokeWidth="1.6" />
  </svg>
)
export const LensIcon = (props: { style?: CSSProperties }) => <Icon component={LensSvg} {...props} />

export function Brand({ sub }: { sub?: string | null }) {
  const { token } = theme.useToken()
  return (
    <Flex align="center" gap="small">
      <Avatar shape="square" size={32} icon={<LensIcon style={{ fontSize: token.fontSizeHeading3 }} />} style={{ background: token.colorPrimary }} />
      <Flex vertical>
        <Typography.Text strong style={{ fontSize: token.fontSizeLG }}>FilingLens</Typography.Text>
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
