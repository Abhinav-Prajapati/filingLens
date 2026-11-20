import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, type AnswerView, type ChatInfo } from './api'

/** The conversation kept in this browser before chats were saved to the account. */
const OLD_THREAD_KEY = 'filinglens.thread.v1'

interface Chats {
  chats: ChatInfo[]
  loaded: boolean
  refresh: () => Promise<void>
  /** After a chat's first answer: refresh now, then a few times while the model names it. */
  watchTitle: (id: string) => void
  rename: (id: string, title: string) => Promise<void>
  remove: (id: string) => Promise<void>
}

const Ctx = createContext<Chats | null>(null)

export function ChatsProvider({ children }: { children: ReactNode }) {
  const [chats, setChats] = useState<ChatInfo[]>([])
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try { setChats(await api.chats()) } catch { /* keep the list shown */ }
    setLoaded(true)
  }, [])

  useEffect(() => {
    void (async () => {
      await importOldThread()
      await refresh()
    })()
  }, [refresh])

  const watchTitle = useCallback((id: string) => {
    void refresh()
    let tries = 0
    const tick = async () => {
      tries += 1
      const list = await api.chats().catch(() => null)
      if (list) setChats(list)
      const c = list?.find((x) => x.id === id)
      if (c && c.title_source === 'question' && tries < 6) setTimeout(tick, 1500)
    }
    setTimeout(tick, 1500)
  }, [refresh])

  const rename = useCallback(async (id: string, title: string) => {
    const c = await api.renameChat(id, title)
    setChats((cs) => cs.map((x) => (x.id === id ? c : x)))
  }, [])

  const remove = useCallback(async (id: string) => {
    await api.deleteChat(id)
    setChats((cs) => cs.filter((x) => x.id !== id))
  }, [])

  const value = useMemo(() => ({ chats, loaded, refresh, watchTitle, rename, remove }), [chats, loaded, refresh, watchTitle, rename, remove])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useChats(): Chats {
  const c = useContext(Ctx)
  if (!c) throw new Error('useChats outside ChatsProvider')
  return c
}

/** Move the old browser-only conversation into the account once, then forget it. */
async function importOldThread() {
  let raw: string | null = null
  try { raw = localStorage.getItem(OLD_THREAD_KEY) } catch { return }
  if (!raw) return
  try {
    const turns = (JSON.parse(raw) as { question: string; answer?: AnswerView }[])
      .filter((t) => t.answer).map((t) => ({ question: t.question, answer: t.answer as AnswerView }))
    if (turns.length) await api.importChat(turns.slice(-50))
  } catch { return }                              // try again next time
  try { localStorage.removeItem(OLD_THREAD_KEY) } catch { /* storage off */ }
}

/** Today / Yesterday / Previous 7 days / Previous 30 days / Older, by last activity. */
export function groupChats(chats: ChatInfo[], now = new Date()): { label: string; chats: ChatInfo[] }[] {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const day = 864e5
  const groups: [string, number][] = [['Today', start], ['Yesterday', start - day], ['Previous 7 days', start - 7 * day], ['Previous 30 days', start - 30 * day], ['Older', -Infinity]]
  const out = groups.map(([label]) => ({ label, chats: [] as ChatInfo[] }))
  for (const c of chats) {
    const t = new Date(c.updated_at).getTime()
    out[groups.findIndex(([, from]) => t >= from)].chats.push(c)
  }
  return out.filter((g) => g.chats.length)
}
