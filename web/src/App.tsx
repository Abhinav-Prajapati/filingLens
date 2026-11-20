import { useEffect, useState } from 'react'
import {
  App as AntApp, Avatar, Button, ConfigProvider, Divider, Dropdown, Flex, Layout, Menu, Result, Spin, Tag, Typography, theme,
} from 'antd'
import {
  DashboardOutlined, DatabaseOutlined, LogoutOutlined, MessageOutlined, ReloadOutlined, UpOutlined, UserOutlined,
} from '@ant-design/icons'
import { AuthProvider, useAuth } from './lib/auth'
import { MetaProvider, useMeta } from './lib/meta'
import { useIsDark } from './lib/theme'
import { AdminData } from './components/AdminData'
import { AskPage, THREAD_KEY } from './components/AskPage'
import { Brand, ThemeSwitch } from './components/Chrome'
import { ExplorePage } from './components/ExplorePage'
import { Welcome } from './components/Welcome'

type Page = 'ask' | 'explore' | 'data'

/** Ant Design's own theme: the default (light) or dark algorithm, nothing overridden. */
export default function App() {
  const dark = useIsDark()
  return (
    <ConfigProvider theme={{ algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm }}>
      <AntApp>
        <PageBackground />
        <AuthProvider>
          <Gate />
        </AuthProvider>
      </AntApp>
    </ConfigProvider>
  )
}

/** The document body takes the layout background, so overscroll never flashes another colour. */
function PageBackground() {
  const { token } = theme.useToken()
  const dark = useIsDark()
  useEffect(() => {
    document.body.style.background = token.colorBgLayout
    document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
  }, [token.colorBgLayout, dark])
  return null
}

/** Signed out: home page and sign-in. Signed in: the app. */
function Gate() {
  const { status, retry } = useAuth()
  if (status === 'loading') {
    return <Flex align="center" justify="center" style={{ minHeight: '100vh' }}><Spin size="large" /></Flex>
  }
  if (status === 'offline') {
    return (
      <Flex align="center" justify="center" style={{ minHeight: '100vh' }}>
        <Result
          status="warning"
          title="Can’t reach the filingLens API"
          subTitle={<>Start it with <Typography.Text code>filinglens serve</Typography.Text>, then try again.</>}
          extra={<Button type="primary" icon={<ReloadOutlined />} onClick={retry}>Try again</Button>}
        />
      </Flex>
    )
  }
  if (status === 'signed-out') return <Welcome />
  return <MetaProvider><Shell /></MetaProvider>
}

function Shell() {
  const { ready, error, retry } = useMeta()
  const { user, signOut } = useAuth()
  const { token } = theme.useToken()
  const isAdmin = user?.role === 'admin'
  const [page, setPage] = useState<Page>(() => {
    const h = location.hash.replace('#', '')
    return h === 'explore' ? 'explore' : h === 'data' && isAdmin ? 'data' : 'ask'
  })
  useEffect(() => { history.replaceState(null, '', (location.search || '') + (page === 'ask' ? '#' : `#${page}`)) }, [page])

  const out = async () => {
    try { localStorage.removeItem(THREAD_KEY) } catch { /* storage off */ }   // the next person starts clean
    history.replaceState(null, '', location.pathname + location.search)
    await signOut()
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Layout.Sider
        width={240}
        breakpoint="lg"
        collapsedWidth={0}
        theme="light"
        style={{ position: 'sticky', top: 0, height: '100vh', borderInlineEnd: `1px solid ${token.colorSplit}` }}
      >
        <Flex vertical style={{ height: '100%' }}>
          <div style={{ padding: `${token.paddingLG}px ${token.padding}px ${token.padding}px` }}><Brand /></div>
          <Menu
            mode="inline"
            selectedKeys={[page]}
            onClick={(e) => setPage(e.key as Page)}
            style={{ borderInlineEnd: 0, flex: 1 }}
            items={[
              { key: 'ask', icon: <MessageOutlined />, label: 'Ask' },
              { key: 'explore', icon: <DashboardOutlined />, label: 'Explore' },
              ...(isAdmin ? [{
                type: 'group' as const, key: 'admin', label: 'Admin',
                children: [{ key: 'data', icon: <DatabaseOutlined />, label: 'Data' }],
              }] : []),
            ]}
          />
          <Divider style={{ margin: 0 }} />
          <Flex vertical gap="small" style={{ padding: token.padding }}>
            <ThemeSwitch block />
            {user && (
              <Dropdown
                trigger={['click']}
                placement="topLeft"
                menu={{
                  items: [
                    { key: 'role', disabled: true, label: <Tag color={isAdmin ? 'blue' : 'default'} style={{ margin: 0 }}>{isAdmin ? 'Admin' : 'User'}</Tag> },
                    { type: 'divider' },
                    { key: 'out', icon: <LogoutOutlined />, label: 'Sign out', onClick: out },
                  ],
                }}
              >
                <Button type="text" block style={{ height: 'auto', padding: token.paddingXS }} aria-label={`Account: ${user.email}`}>
                  <Flex align="center" gap="small" style={{ width: '100%', minWidth: 0 }}>
                    <Avatar size="small" icon={<UserOutlined />} />
                    <Flex vertical align="flex-start" style={{ minWidth: 0, flex: 1 }}>
                      <Typography.Text ellipsis style={{ maxWidth: '100%' }}>{user.email}</Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM }}>{isAdmin ? 'Admin' : 'User'}</Typography.Text>
                    </Flex>
                    <UpOutlined style={{ color: token.colorTextTertiary, fontSize: token.fontSizeSM }} />
                  </Flex>
                </Button>
              </Dropdown>
            )}
          </Flex>
        </Flex>
      </Layout.Sider>
      <Layout>
        <Layout.Content style={{ padding: token.paddingLG, minWidth: 0 }}>
          {!ready ? null : error ? (
            <Result
              status="warning"
              title="Can’t reach the filingLens API"
              subTitle={<>{error} <Typography.Text code>filinglens serve</Typography.Text></>}
              extra={<Button type="primary" icon={<ReloadOutlined />} onClick={retry}>Try again</Button>}
            />
          ) : page === 'ask' ? <AskPage /> : page === 'explore' ? <ExplorePage /> : isAdmin ? <AdminData /> : null}
        </Layout.Content>
      </Layout>
    </Layout>
  )
}
