import { Segmented } from 'antd'
import { DesktopOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons'
import { setThemePref, useThemePref, type ThemePref } from '../lib/theme'

/** The filingLens mark and wordmark. */
export function Brand({ sub = 'NSE FILINGS · VERIFIED' }: { sub?: string | null }) {
  return (
    <div className="brand">
      <svg viewBox="0 0 32 32" aria-hidden>
        <rect width="32" height="32" rx="8" fill="var(--ink)" />
        <path d="M9 23V9h9M9 16h6" stroke="var(--page)" strokeWidth="2.6" fill="none" strokeLinecap="round" />
        <circle cx="22.5" cy="20.5" r="3.6" stroke="var(--accent)" strokeWidth="2.4" fill="none" />
      </svg>
      <div>
        <div className="brand-name">filing<em>Lens</em></div>
        {sub && <div className="brand-sub">{sub}</div>}
      </div>
    </div>
  )
}

export function ThemeSwitch() {
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
