import { useEffect, useRef, useState, type ReactNode } from 'react'
import { CaretDownFilled, CaretUpFilled, MinusOutlined } from '@ant-design/icons'
import { animate } from 'motion'
import { motion, useReducedMotion } from 'motion/react'

/** A number that counts up to its value once it appears. */
export function CountUp({ value, format, duration = 1.1 }: { value: number; format: (v: number) => string; duration?: number }) {
  const [shown, setShown] = useState(() => format(0))
  const prev = useRef(0)
  const fmt = useRef(format)
  fmt.current = format
  useEffect(() => {
    const format = (v: number) => fmt.current(v)
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduce) { setShown(format(value)); prev.current = value; return }
    const ctl = animate(prev.current, value, {           // from wherever the number is now
      duration, ease: [0.16, 1, 0.3, 1],
      onUpdate: (v) => { prev.current = v; setShown(format(v)) },
    })
    return () => ctl.stop()
  }, [value, duration])
  return <>{shown}</>
}

export function Legend({ items, line }: { items: { label: string; color: string }[]; line?: boolean }) {
  if (items.length < 2) return null
  return (
    <div className="legend" aria-label="Legend">
      {items.map((i) => (
        <span key={i.label}><i className={line ? 'line' : ''} style={{ background: i.color }} />{i.label}</span>
      ))}
    </div>
  )
}

export function Delta({ value, unit = '%', goodWhenUp = true }: { value: number | null | undefined; unit?: '%' | 'pp'; goodWhenUp?: boolean }) {
  if (value == null) return null
  const dir = Math.abs(value) < 0.005 ? 'flat' : (value > 0) === goodWhenUp ? 'up' : 'down'
  const arrow = value > 0 ? <CaretUpFilled /> : value < 0 ? <CaretDownFilled /> : <MinusOutlined />
  return (
    <span className={`delta ${dir}`}>
      <span aria-hidden style={{ fontSize: 10 }}>{arrow}</span>
      {value > 0 ? '+' : value < 0 ? '−' : ''}{Math.abs(value).toFixed(unit === 'pp' ? 2 : 1)}{unit === 'pp' ? ' pp' : '%'}
    </span>
  )
}

/** Fades and lifts children in, staggered by `i`. */
export function Rise({ children, i = 0, className }: { children: ReactNode; i?: number; className?: string }) {
  const reduce = useReducedMotion()
  if (reduce) return <div className={className}>{children}</div>
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, delay: i * 0.06, ease: [0.16, 1, 0.3, 1] }}
    >
      {children}
    </motion.div>
  )
}
