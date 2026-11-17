// frontend/src/pages/LoginPage.tsx
import { useState, type FormEvent } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useAuth } from '../auth'

export default function LoginPage() {
  const { needBootstrap, login, bootstrap } = useAuth()
  const nav = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null); setBusy(true)
    try {
      if (needBootstrap) await bootstrap(username, password)
      else await login(username, password)
      nav('/', { replace: true })
    } catch (err) {
      const msg = err instanceof Error ? err.message : '登录失败'
      // sendJson 抛的 Error 文本可能含后端 {"error": "..."} —— 尽量提取中文
      const m = msg.match(/"error"\s*:\s*"([^"]+)"/)
      setError(m ? m[1] : msg)
    } finally { setBusy(false) }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface-900 p-4">
      <form onSubmit={onSubmit}
            className="w-full max-w-sm rounded-xl border border-surface-700 bg-surface-850 p-6 space-y-4">
        <div className="text-center">
          <div className="text-2xl">🧊</div>
          <h1 className="mt-1 text-lg font-semibold text-slate-100">
            {needBootstrap ? '创建管理员账号' : '登录 astock-hub'}
          </h1>
          <p className="mt-1 text-xs text-slate-400">
            {needBootstrap ? '首次使用：设置你的管理员用户名与密码'
                          : '用分配给你的账号登录'}
          </p>
        </div>
        <input value={username} onChange={(e) => setUsername(e.target.value)}
               placeholder="用户名" autoComplete="username" required
               className="w-full rounded-md border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-slate-100" />
        <input value={password} onChange={(e) => setPassword(e.target.value)}
               type="password" placeholder="密码" autoComplete={needBootstrap ? 'new-password' : 'current-password'}
               required minLength={8}
               className="w-full rounded-md border border-surface-700 bg-surface-900 px-3 py-2 text-sm text-slate-100" />
        {error && <div className="text-xs text-red-400">{error}</div>}
        <button disabled={busy}
                className="w-full rounded-md bg-sky-600 px-3 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50">
          {busy ? '请稍候…' : (needBootstrap ? '创建并进入' : '登录')}
        </button>
        {!needBootstrap && (
          <div className="text-center text-xs text-slate-400">
            没有账号？<Link to="/register" className="text-sky-400 hover:underline">用注册密钥注册</Link>
          </div>
        )}
      </form>
    </div>
  )
}
