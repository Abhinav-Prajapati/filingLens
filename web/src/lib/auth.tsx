import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { ApiError, UNAUTHORIZED, auth, clearCache, type User } from './api'

type Status = 'loading' | 'signed-out' | 'signed-in' | 'offline'

interface Auth {
  status: Status
  user: User | null
  signIn: (email: string, password: string) => Promise<void>
  signUp: (email: string, password: string) => Promise<void>
  signOut: () => Promise<void>
  retry: () => void
}

const Ctx = createContext<Auth | null>(null)

/** Who is signed in. The session is an HttpOnly cookie the API sets; this only asks /auth/me. */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>('loading')
  const [user, setUser] = useState<User | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let live = true
    auth.me()
      .then((u) => { if (live) { setUser(u); setStatus('signed-in') } })
      .catch((e: unknown) => {
        if (!live) return
        setUser(null)
        setStatus(e instanceof ApiError && e.status === 0 ? 'offline' : 'signed-out')
      })
    return () => { live = false }
  }, [attempt])

  // any data request answered 401: the session expired or was ended elsewhere
  useEffect(() => {
    const out = () => { clearCache(); setUser(null); setStatus('signed-out') }
    window.addEventListener(UNAUTHORIZED, out)
    return () => window.removeEventListener(UNAUTHORIZED, out)
  }, [])

  const signIn = useCallback(async (email: string, password: string) => {
    await auth.login(email, password)
    clearCache()
    setUser(await auth.me())
    setStatus('signed-in')
  }, [])

  const signUp = useCallback(async (email: string, password: string) => {
    await auth.register(email, password)
    await signIn(email, password)
  }, [signIn])

  const signOut = useCallback(async () => {
    try { await auth.logout() } catch { /* already signed out */ }
    clearCache()
    setUser(null)
    setStatus('signed-out')
  }, [])

  const value = useMemo<Auth>(() => ({
    status, user, signIn, signUp, signOut, retry: () => { setStatus('loading'); setAttempt((a) => a + 1) },
  }), [status, user, signIn, signUp, signOut])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAuth(): Auth {
  const a = useContext(Ctx)
  if (!a) throw new Error('useAuth outside AuthProvider')
  return a
}
