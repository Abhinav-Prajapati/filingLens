import { useEffect, useMemo, useState } from 'react'
import { AnimatePresence, MotionConfig, motion } from 'motion/react'
import { App as AntApp, Badge, Button, ConfigProvider, Layout, Menu, Popconfirm, Result, Segmented, Tag, Tooltip, theme, type ThemeConfig } from 'antd'
import {
  DashboardOutlined, DeleteOutlined, DesktopOutlined, MessageOutlined, MoonOutlined, ReloadOutlined, SunOutlined,
} from '@ant-design/icons'
import { MetaProvider, useMeta } from './lib/meta'
import { setThemePref, useIsDark, useThemePref, useTokens, seriesColor, type ThemePref } from './lib/theme'
import { AskPage, THREAD_KEY } from './components/AskPage'
import { ExplorePage } from './components/ExplorePage'

type Page = 'ask' | 'explore'

const SANS = "'IBM Plex Sans', system-ui, -apple-system, 'Segoe UI', sans-serif"
const MONO = "'IBM Plex Mono', ui-monospace, Menlo, monospace"

/** antd theme built from the same palette the charts use, so components and charts match. */
function antdTheme(dark: boolean): ThemeConfig {
  const c = dark
    ? { page: '#0f0f0e', surface: '#181816', surface2: '#201f1d', ink: '#f0efec', ink2: '#c3c2b7', muted: '#8c8a82', rule: '#2a2a27', rule2: '#3a3935', accent: '#e0b354', wash: '#e0b3541a' }
    : { page: '#f4f2ec', surface: '#fbfaf7', surface2: '#f0eee7', ink: '#16161a', ink2: '#4e4d48', muted: '#86847c', rule: '#e3e0d6', rule2: '#cfcbbf', accent: '#9a6b0a', wash: '#9a6b0a14' }
  return {
    algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token: {
      colorPrimary: c.accent,
      colorInfo: dark ? '#3987e5' : '#2a78d6',
      colorSuccess: dark ? '#2fb52f' : '#006300',
      colorError: dark ? '#f07a6e' : '#b42318',
      colorBgLayout: c.page,
      colorBgContainer: c.surface,
      colorBgElevated: dark ? '#1f1f1c' : '#ffffff',
      colorFillAlter: c.surface2,
      colorBorder: c.rule2,
      colorBorderSecondary: c.rule,
      colorSplit: c.rule,
      colorText: c.ink,
      colorTextSecondary: c.ink2,
      colorTextTertiary: c.muted,
      colorTextDescription: c.muted,
      fontFamily: SANS,
      fontFamilyCode: MONO,
      fontSize: 14,
      borderRadius: 10,
      borderRadiusSM: 6,
      controlHeight: 34,
      wireframe: false,
    },
    components: {
      Layout: { siderBg: dark ? '#131312' : '#efece5', headerBg: 'transparent', bodyBg: c.page },
      Menu: {
        itemBg: 'transparent', itemSelectedBg: c.wash, itemSelectedColor: c.ink, itemColor: c.ink2,
        itemHoverBg: c.surface2, itemBorderRadius: 8, itemHeight: 38, itemMarginInline: 0,
      },
      Button: { primaryColor: dark ? '#17150f' : '#ffffff', primaryShadow: 'none', defaultShadow: 'none' },
      Card: { headerBg: 'transparent', headerFontSize: 14, colorBorderSecondary: c.rule },
      Table: { headerBg: c.surface2, headerColor: c.muted, rowHoverBg: c.surface2, borderColor: c.rule, headerSplitColor: 'transparent', cellPaddingBlockSM: 9 },
      Segmented: { itemSelectedBg: dark ? '#2c2b27' : '#ffffff', trackBg: c.surface2, itemColor: c.muted, itemHoverColor: c.ink, itemSelectedColor: c.ink },
      Tag: { defaultBg: c.surface2, defaultColor: c.ink2 },
      Statistic: { contentFontSize: 26, titleFontSize: 12.5 },
      Collapse: { headerPadding: '10px 0', contentPadding: '0 0 6px', headerBg: 'transparent' },
      Alert: { withDescriptionPadding: '12px 16px' },
      Steps: { dotSize: 8 },
      Tooltip: { colorBgSpotlight: dark ? '#2c2b27' : '#16161a' },
    },
  }
}

export default function App() {
  const dark = useIsDark()
  const config = useMemo(() => antdTheme(dark), [dark])
  return (
    <ConfigProvider theme={config}>
      <AntApp>
        <MotionConfig reducedMotion="user">
          <MetaProvider>
            <Shell />
          </MetaProvider>
        </MotionConfig>
      </AntApp>
    </ConfigProvider>
  )
}

const PAGES: Record<Page, { title: string; sub: string }> = {
  ask: { title: 'Ask', sub: 'Questions in English or Hinglish, answered from filed figures' },
  explore: { title: 'Explore', sub: 'Size, growth, margins and cost structure across coverage' },
}

function Shell() {
  const { ready, error, retry } = useMeta()
  const [page, setPage] = useState<Page>(() => (location.hash === '#explore' ? 'explore' : 'ask'))
  useEffect(() => { history.replaceState(null, '', (location.search || '') + (page === 'explore' ? '#explore' : '#')) }, [page])

  const clear = () => {
    try { localStorage.removeItem(THREAD_KEY) } catch { /* storage off */ }
    location.reload()
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Side page={page} setPage={setPage} />
      <Layout>
        <Layout.Header className="app-header">
          <div className="crumb">
            <h1>{PAGES[page].title}</h1>
            <span>{PAGES[page].sub}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {page === 'ask' && (
              <Popconfirm title="Clear the conversation?" okText="Clear" cancelText="Keep" onConfirm={clear}>
                <Tooltip title="Clear the conversation"><Button type="text" icon={<DeleteOutlined />} aria-label="Clear the conversation" /></Tooltip>
              </Popconfirm>
            )}
            <ThemeSwitch />
          </div>
        </Layout.Header>
        <Layout.Content className="app-content">
          {!ready ? null : error ? (
            <Result
              status="warning"
              title="Can’t reach the filingLens API"
              subTitle={<>{error}<br /><code>filinglens serve</code></>}
              extra={<Button type="primary" icon={<ReloadOutlined />} onClick={retry}>Try again</Button>}
            />
          ) : (
            <AnimatePresence mode="wait">
              <motion.div
                key={page}
                initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.22 }}
                style={{ minHeight: 'calc(100vh - 84px)', display: 'flex', flexDirection: 'column' }}
              >
                {page === 'ask' ? <AskPage /> : <ExplorePage />}
              </motion.div>
            </AnimatePresence>
          )}
        </Layout.Content>
      </Layout>
    </Layout>
  )
}

function ThemeSwitch() {
  const pref = useThemePref()
  return (
    <Segmented<ThemePref>
      size="small"
      value={pref}
      onChange={setThemePref}
      options={[
        { value: 'dark', icon: <MoonOutlined />, title: 'Dark' },
        { value: 'light', icon: <SunOutlined />, title: 'Light' },
        { value: 'system', icon: <DesktopOutlined />, title: 'Follow the system' },
      ]}
      aria-label="Theme"
    />
  )
}

function Side({ page, setPage }: { page: Page; setPage: (p: Page) => void }) {
  const { companies, health, error } = useMeta()
  const t = useTokens()
  return (
    <Layout.Sider className="app-sider" width={268} breakpoint="lg" collapsedWidth={0} theme={t.dark ? 'dark' : 'light'}>
      <div className="brand">
        <svg viewBox="0 0 32 32" aria-hidden>
          <rect width="32" height="32" rx="8" fill="var(--ink)" />
          <path d="M9 23V9h9M9 16h6" stroke="var(--page)" strokeWidth="2.6" fill="none" strokeLinecap="round" />
          <circle cx="22.5" cy="20.5" r="3.6" stroke="var(--accent)" strokeWidth="2.4" fill="none" />
        </svg>
        <div>
          <div className="brand-name">filing<em>Lens</em></div>
          <div className="brand-sub">NSE FILINGS · VERIFIED</div>
        </div>
      </div>

      <Menu
        mode="inline"
        selectedKeys={[page]}
        onClick={(e) => setPage(e.key as Page)}
        items={[
          { key: 'ask', icon: <MessageOutlined />, label: 'Ask' },
          { key: 'explore', icon: <DashboardOutlined />, label: 'Explore' },
        ]}
      />

      <section aria-label="Coverage">
        <div className="eyebrow side-label">Coverage</div>
        {companies.map((c) => (
          <div key={c.ticker} className={`co ${c.has_data ? '' : 'off'}`}>
            <span className="swatch round" style={{ background: c.has_data ? seriesColor(t, c.ticker, 0) : 'var(--rule-strong)' }} />
            <span className="co-name" title={c.name}>{c.name}</span>
            <div className="co-meta">
              <b>{c.ticker}</b>
              {c.has_data ? (
                <>
                  <span>{c.fys[0]}–{c.latest_fy}</span>
                  {c.latest_quarter && <span>{c.latest_quarter}</span>}
                </>
              ) : <span>not ingested</span>}
            </div>
            {c.has_data && (
              <div className="co-meta" style={{ marginTop: 3 }}>
                {c.annual_reports.length
                  ? <Tag variant="outlined" style={{ fontSize: 10, lineHeight: '16px', paddingInline: 5, margin: 0 }}>AR {c.annual_reports.join(' · ')}</Tag>
                  : <span>figures only, no report text</span>}
              </div>
            )}
          </div>
        ))}
      </section>

      <div className="side-foot">
        <Badge
          status={error ? 'error' : health ? 'success' : 'processing'}
          text={<span className="muted" style={{ fontSize: 12 }}>{error ? 'API offline' : health ? `${health.facts.toLocaleString('en-IN')} facts · ${health.chunks.toLocaleString('en-IN')} passages` : 'Connecting…'}</span>}
        />
        {health && (
          <Tooltip title={health.llm ?? 'Questions are understood by rules'} placement="right">
            <Badge status={health.llm ? 'default' : 'warning'} text={<span className="muted" style={{ fontSize: 12 }}>{health.llm ? `LLM · ${health.llm.split(' @ ')[0]}` : 'LLM not configured'}</span>} />
          </Tooltip>
        )}
      </div>
    </Layout.Sider>
  )
}
