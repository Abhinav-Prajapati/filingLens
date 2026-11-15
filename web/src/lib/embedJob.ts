import { useCallback, useEffect, useRef, useState } from 'react'
import { admin, type EmbedDevice, type EmbedJob } from './api'

const RUNNING: EmbedJob['state'][] = ['loading', 'running']
export const embedding = (j: EmbedJob | null) => !!j && RUNNING.includes(j.state)

/** The embedding job, polled while it runs; ``onFinished`` fires once when it ends. */
export function useEmbedJob(onFinished: (j: EmbedJob) => void) {
  const [job, setJob] = useState<EmbedJob | null>(null)
  const [polling, setPolling] = useState(true)
  const prev = useRef<EmbedJob['state'] | null>(null)
  const finished = useRef(onFinished)
  useEffect(() => { finished.current = onFinished })

  useEffect(() => {
    if (!polling) return
    let live = true
    let timer: number | undefined
    const tick = async () => {
      let j: EmbedJob | null = null
      try { j = await admin.embedJob() } catch { /* keep polling */ }
      if (!live) return
      setJob(j)
      if (j && prev.current && RUNNING.includes(prev.current) && !RUNNING.includes(j.state)) finished.current(j)
      prev.current = j?.state ?? null
      if (embedding(j)) timer = window.setTimeout(tick, 1200)
      else setPolling(false)
    }
    tick()
    return () => { live = false; window.clearTimeout(timer) }
  }, [polling])

  const start = useCallback(async (body: { doc_ids?: string[]; tickers?: string[]; device: EmbedDevice }) => {
    const j = await admin.embed(body)
    prev.current = j.state
    setJob(j)
    setPolling(true)
    return j
  }, [])

  const stop = useCallback(async () => { setJob(await admin.stopEmbed()) }, [])
  return { job, start, stop }
}

