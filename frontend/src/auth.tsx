// frontend/src/auth.tsx
import { createContext, useCallback, useContext, useEffect, useState,
         type ReactNode } from 'react'
import { getJson, sendJson } from './lib/api'

export type Perms = Record<string, boolean>
export interface SessionUser {
  id: string; username: string; is_admin: boolean; perms: Perms
}
interface SessionResp {
  user: SessionUser | null
  need_bootstrap: boolean
  features?: { tq?: boolean }
}
export interface PanelFeatures { tq?: boolean }

interface AuthState {
  user: SessionUser | null
  loading: boolean
  needBootstrap: boolean
  features: PanelFeatures
  can: (perm: string) => boolean
  isAdmin: boolean
  login: (username: string, password: string) => Promise<void>
  register: (username: string, password: string, inviteKey: string) => Promise<void>
  bootstrap: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const Ctx = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<SessionUser | null>(null)
  const [loading, setLoading] = useState(true)
  const [needBootstrap, setNeedBootstrap] = useState(false)
  const [features, setFeatures] = useState<PanelFeatures>({})

  const refresh = useCallback(async () => {
    try {
      const s = await getJson<SessionResp>('/api/auth/session')
      setUser(s.user); setNeedBootstrap(s.need_bootstrap)
      setFeatures(s.features ?? {})
    } catch { setUser(null) }
    finally { setLoading(false) }
  }, [])

  useEffect(() => {
    refresh()
    const on401 = () => setUser(null)
    window.addEventListener('ah:401', on401)
    return () => window.removeEventListener('ah:401', on401)
  }, [refresh])

  const login = useCallback(async (username: string, password: string) => {
    await sendJson('POST', '/api/auth/login', { username, password })
    await refresh()
  }, [refresh])
  const register = useCallback(async (username: string, password: string, inviteKey: string) => {
    await sendJson('POST', '/api/auth/register', { username, password, invite_key: inviteKey })
    await refresh()
  }, [refresh])
  const bootstrap = useCallback(async (username: string, password: string) => {
    await sendJson('POST', '/api/auth/bootstrap', { username, password })
    await refresh()
  }, [refresh])
  const logout = useCallback(async () => {
    await sendJson('POST', '/api/auth/logout')
    setUser(null)
  }, [])

  const can = useCallback((perm: string) =>
    !!user && (user.is_admin || !!user.perms[perm]), [user])

  return (
    <Ctx.Provider value={{ user, loading, needBootstrap, can, features,
                          isAdmin: !!user?.is_admin,
                          login, register, bootstrap, logout }}>
      {children}
    </Ctx.Provider>
  )
}

export function useAuth(): AuthState {
  const v = useContext(Ctx)
  if (!v) throw new Error('useAuth 必须在 AuthProvider 内')
  return v
}
