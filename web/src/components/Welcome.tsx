import { useEffect, useState } from 'react'
import {
  Alert, Avatar, Button, Card, Col, Divider, Flex, Form, Input, Layout, Row, Space, Statistic, Typography, theme,
} from 'antd'
import {
  ArrowLeftOutlined, FileSearchOutlined, LockOutlined, MailOutlined, SafetyCertificateOutlined, StockOutlined,
  TranslationOutlined,
} from '@ant-design/icons'
import { api, type Health } from '../lib/api'
import { useAuth } from '../lib/auth'
import { Brand, ThemeSwitch } from './Chrome'

export type PublicView = 'home' | 'login' | 'register'

const FEATURES = [
  {
    icon: <SafetyCertificateOutlined />, title: 'Filed figures, not estimates',
    text: 'Every number comes from NSE XBRL filings and is matched to its annual-report page where possible. Missing data stays missing.',
  },
  {
    icon: <StockOutlined />, title: 'Compare and chart',
    text: 'Trends, peers, margins and cost structure as charts and sortable tables, with growth and CAGR worked out.',
  },
  {
    icon: <FileSearchOutlined />, title: 'Search the annual reports',
    text: 'Ask about strategy, risks or attrition and read the matching passages, with the report and page they came from.',
  },
  {
    icon: <TranslationOutlined />, title: 'English or Hinglish',
    text: 'Type questions the way you would ask a colleague. Every answer shows its sources.',
  },
]

/** Signed-out part of the app: the home page and the sign-in / sign-up forms. */
export function Welcome() {
  const { token } = theme.useToken()
  const [view, setView] = useState<PublicView>(() => viewFromHash())
  useEffect(() => {
    const on = () => setView(viewFromHash())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  const go = (v: PublicView) => { location.hash = v === 'home' ? '' : v; setView(v) }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Layout.Header
        style={{
          background: token.colorBgContainer, borderBottom: `1px solid ${token.colorSplit}`,
          paddingInline: token.paddingLG, position: 'sticky', top: 0, zIndex: 10,
        }}
      >
        <Flex align="center" justify="space-between" style={{ height: '100%' }}>
          <Typography.Link onClick={() => go('home')} aria-label="FilingLens home"><Brand sub={null} /></Typography.Link>
          <Space>
            <ThemeSwitch />
            {view !== 'login' && <Button onClick={() => go('login')}>Sign in</Button>}
            {view !== 'register' && <Button type="primary" onClick={() => go('register')}>Create account</Button>}
          </Space>
        </Flex>
      </Layout.Header>
      <Layout.Content style={{ padding: token.paddingLG }}>
        {view === 'home' ? <Home go={go} /> : <AuthForm mode={view} go={go} />}
      </Layout.Content>
      <Layout.Footer style={{ textAlign: 'center' }}>
        <Typography.Text type="secondary">FilingLens · figures from NSE filings and company annual reports</Typography.Text>
      </Layout.Footer>
    </Layout>
  )
}

function viewFromHash(): PublicView {
  const h = location.hash.replace('#', '')
  return h === 'login' || h === 'register' ? h : 'home'
}

function Home({ go }: { go: (v: PublicView) => void }) {
  const { token } = theme.useToken()
  const [health, setHealth] = useState<Health | null>(null)
  useEffect(() => { api.health().then(setHealth).catch(() => {}) }, [])
  return (
    <Flex vertical gap="large" style={{ maxWidth: 1200, margin: '0 auto', paddingBlock: token.paddingXL }}>
      <Flex vertical gap="middle" style={{ maxWidth: 720 }}>
        <Typography.Title style={{ margin: 0 }}>Indian company financials, checked against the filings</Typography.Title>
        <Typography.Paragraph type="secondary" style={{ fontSize: token.fontSizeLG, margin: 0 }}>
          Ask about revenue, margins, growth or what an annual report says, and get the figures as filed, with charts,
          tables and the page every number came from.
        </Typography.Paragraph>
        <Space>
          <Button type="primary" size="large" onClick={() => go('register')}>Create a free account</Button>
          <Button size="large" onClick={() => go('login')}>Sign in</Button>
        </Space>
      </Flex>

      {health && (
        <Card>
          <Row gutter={[24, 16]}>
            <Col xs={24} sm={8}><Statistic title="Companies loaded" value={health.companies_with_data} /></Col>
            <Col xs={24} sm={8}><Statistic title="Filed facts" value={health.facts} /></Col>
            <Col xs={24} sm={8}><Statistic title="Report passages" value={health.chunks} /></Col>
          </Row>
        </Card>
      )}

      <Row gutter={[16, 16]}>
        {FEATURES.map((f) => (
          <Col key={f.title} xs={24} sm={12} lg={6}>
            <Card style={{ height: '100%' }}>
              <Card.Meta
                avatar={<Avatar icon={f.icon} style={{ background: token.colorPrimaryBg, color: token.colorPrimary }} />}
                title={f.title}
                description={f.text}
              />
            </Card>
          </Col>
        ))}
      </Row>
    </Flex>
  )
}

interface Fields { email: string; password: string; confirm?: string }

function AuthForm({ mode, go }: { mode: 'login' | 'register'; go: (v: PublicView) => void }) {
  const { token } = theme.useToken()
  const { signIn, signUp } = useAuth()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const signUpMode = mode === 'register'

  const submit = async ({ email, password }: Fields) => {
    setBusy(true); setError(null)
    try {
      await (signUpMode ? signUp : signIn)(email.trim(), password)
      location.hash = ''
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }

  return (
    <Flex justify="center" style={{ paddingBlock: token.paddingXL }}>
      <Card style={{ width: '100%', maxWidth: 400 }}>
        <Button type="link" icon={<ArrowLeftOutlined />} onClick={() => go('home')} style={{ paddingInline: 0 }}>
          Home
        </Button>
        <Typography.Title level={3} style={{ marginTop: token.marginXS }}>
          {signUpMode ? 'Create your account' : 'Sign in'}
        </Typography.Title>
        <Typography.Paragraph type="secondary">
          {signUpMode ? 'It takes a few seconds; you can start asking straight after.' : 'Sign in to ask questions and explore the data.'}
        </Typography.Paragraph>
        {error && <Alert type="error" showIcon title={error} style={{ marginBottom: token.marginMD }} />}
        <Form<Fields> layout="vertical" requiredMark={false} onFinish={submit} disabled={busy} key={mode}>
          <Form.Item name="email" label="Email" rules={[{ required: true, message: 'Enter your email' }, { type: 'email', message: 'That is not a valid email address' }]}>
            <Input prefix={<MailOutlined />} autoComplete="email" autoFocus placeholder="you@example.com" />
          </Form.Item>
          <Form.Item
            name="password"
            label="Password"
            extra={signUpMode ? 'At least 8 characters.' : undefined}
            rules={[{ required: true, message: 'Enter your password' }, ...(signUpMode ? [{ min: 8, message: 'Use at least 8 characters' }] : [])]}
          >
            <Input.Password prefix={<LockOutlined />} autoComplete={signUpMode ? 'new-password' : 'current-password'} />
          </Form.Item>
          {signUpMode && (
            <Form.Item
              name="confirm"
              label="Confirm password"
              dependencies={['password']}
              rules={[
                { required: true, message: 'Enter the password again' },
                ({ getFieldValue }) => ({
                  validator: (_, v) => (!v || v === getFieldValue('password') ? Promise.resolve() : Promise.reject(new Error('The passwords differ'))),
                }),
              ]}
            >
              <Input.Password prefix={<LockOutlined />} autoComplete="new-password" />
            </Form.Item>
          )}
          <Button type="primary" htmlType="submit" block loading={busy}>
            {signUpMode ? 'Create account' : 'Sign in'}
          </Button>
        </Form>
        <Divider plain>{signUpMode ? 'Already have an account?' : 'New to FilingLens?'}</Divider>
        <Button block onClick={() => go(signUpMode ? 'login' : 'register')}>
          {signUpMode ? 'Sign in' : 'Create an account'}
        </Button>
      </Card>
    </Flex>
  )
}
