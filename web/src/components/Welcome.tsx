import { useEffect, useState } from 'react'
import { motion } from 'motion/react'
import { Alert, Button, Card, Form, Input, Tag } from 'antd'
import {
  ArrowLeftOutlined, ArrowRightOutlined, FileSearchOutlined, LockOutlined, MailOutlined, SafetyCertificateOutlined,
  StockOutlined, TranslationOutlined,
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
    text: 'Trends, peers, margins and cost structure, as animated charts and sortable tables, with growth and CAGR worked out.',
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
  const [view, setView] = useState<PublicView>(() => viewFromHash())
  useEffect(() => {
    const on = () => setView(viewFromHash())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  const go = (v: PublicView) => { location.hash = v === 'home' ? '' : v; setView(v) }

  return (
    <div className="welcome">
      <header className="welcome-bar">
        <a href="#" onClick={(e) => { e.preventDefault(); go('home') }} aria-label="filingLens home"><Brand sub={null} /></a>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <ThemeSwitch />
          {view !== 'login' && <Button type="text" onClick={() => go('login')}>Sign in</Button>}
          {view !== 'register' && <Button type="primary" onClick={() => go('register')}>Create account</Button>}
        </div>
      </header>
      <motion.main key={view} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
        {view === 'home' ? <Home go={go} /> : <AuthForm mode={view} go={go} />}
      </motion.main>
      <footer className="welcome-foot">filingLens · figures from NSE filings and company annual reports</footer>
    </div>
  )
}

function viewFromHash(): PublicView {
  const h = location.hash.replace('#', '')
  return h === 'login' || h === 'register' ? h : 'home'
}

function Home({ go }: { go: (v: PublicView) => void }) {
  const [health, setHealth] = useState<Health | null>(null)
  useEffect(() => { api.health().then(setHealth).catch(() => {}) }, [])
  return (
    <div className="home">
      <section className="home-hero">
        <Tag color="gold" variant="outlined" style={{ fontFamily: 'var(--mono)', letterSpacing: '0.06em' }}>RESEARCH DESK</Tag>
        <h2>Indian company financials, <em>checked against the filings.</em></h2>
        <p className="intro-lede">
          Ask about revenue, margins, growth or what an annual report says, and get the figures as filed,
          with charts, tables and the page every number came from.
        </p>
        <div className="home-cta">
          <Button type="primary" size="large" onClick={() => go('register')} icon={<ArrowRightOutlined />} iconPlacement="end">
            Create a free account
          </Button>
          <Button size="large" onClick={() => go('login')}>Sign in</Button>
        </div>
        {health && (
          <div className="home-facts">
            <span><b className="num">{health.companies_with_data}</b> companies loaded</span>
            <span><b className="num">{health.facts.toLocaleString('en-IN')}</b> filed facts</span>
            <span><b className="num">{health.chunks.toLocaleString('en-IN')}</b> report passages</span>
          </div>
        )}
      </section>
      <section className="home-features" aria-label="What filingLens does">
        {FEATURES.map((f, i) => (
          <motion.div key={f.title} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.08 * i + 0.1 }}>
            <Card className="home-feature">
              <span className="prompt-icon">{f.icon}</span>
              <h3>{f.title}</h3>
              <p>{f.text}</p>
            </Card>
          </motion.div>
        ))}
      </section>
    </div>
  )
}

interface Fields { email: string; password: string; confirm?: string }

function AuthForm({ mode, go }: { mode: 'login' | 'register'; go: (v: PublicView) => void }) {
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
    <div className="auth-wrap">
      <Card className="auth-card" styles={{ body: { padding: 28 } }}>
        <Button type="link" size="small" icon={<ArrowLeftOutlined />} onClick={() => go('home')} style={{ paddingInline: 0, marginBottom: 12 }}>
          Home
        </Button>
        <h2 className="auth-title">{signUpMode ? 'Create your account' : 'Welcome back'}</h2>
        <p className="muted" style={{ margin: '4px 0 22px' }}>
          {signUpMode ? 'It takes a few seconds. You can start asking straight after.' : 'Sign in to ask questions and explore the data.'}
        </p>
        {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 16 }} />}
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
          <Button type="primary" htmlType="submit" block size="large" loading={busy} style={{ marginTop: 4 }}>
            {signUpMode ? 'Create account' : 'Sign in'}
          </Button>
        </Form>
        <p className="auth-switch">
          {signUpMode ? 'Already have an account?' : 'New to filingLens?'}{' '}
          <Button type="link" size="small" style={{ padding: 0 }} onClick={() => go(signUpMode ? 'login' : 'register')}>
            {signUpMode ? 'Sign in' : 'Create an account'}
          </Button>
        </p>
      </Card>
    </div>
  )
}
