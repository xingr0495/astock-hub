import { Routes, Route, Navigate } from 'react-router-dom'
import type { ReactNode } from 'react'
import { AuthProvider, useAuth } from './auth'
import Layout from './components/Layout'
import StockAnalysis from './pages/StockAnalysis'
import Dashboard from './pages/Dashboard'
import StrategyScreen from './pages/StrategyScreen'
import NewsPage from './pages/NewsPage'
import SectorAnalysis from './pages/SectorAnalysis'
import WatchlistPage from './pages/WatchlistPage'
import ReviewPage from './pages/ReviewPage'
import NotifyPage from './pages/NotifyPage'
import DataSourcesPage from './pages/DataSourcesPage'
import LlmSettingsPage from './pages/LlmSettingsPage'

function Spinner() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-surface-900 text-slate-400">
      加载中…
    </div>
  )
}

// 路由守卫。perm 语义：
//   perm="__admin__"  —— 哨兵值（不是真实权限键）：该路由仅管理员可进，不查 perms 表；
//   perm="watchlist"  —— 普通权限键：管理员直通，普通用户须 perms[perm] 为真；
//   无 perm           —— view_market 默认档路由（/dashboard /screen 等），
//                        前端不拦（默认开），后端中间件兜底。
function Require({ perm, feature, children }: {
  perm?: string; feature?: string; children: ReactNode
}) {
  const { can, isAdmin, features } = useAuth()
  const needAdmin = perm === '__admin__'
  if (needAdmin && !isAdmin) return <Navigate to="/" replace />
  if (perm && !needAdmin && !isAdmin && !can(perm)) return <Navigate to="/" replace />
  // 功能开关（如云端 tq=false 隐藏交易页）：无条件重定向，管理员也不例外
  if (feature && !features[feature]) return <Navigate to="/" replace />
  return <>{children}</>
}

function Root() {
  const { user, loading } = useAuth()
  if (loading) return <Spinner />
  if (!user) return <div className="p-8 text-center text-sm text-slate-400">后端未连接，请确认面板服务已启动</div>
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<StockAnalysis />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/market" element={<Navigate to="/dashboard" replace />} />
        <Route path="/sector" element={<SectorAnalysis />} />
        <Route path="/watchlist" element={<Require perm="watchlist"><WatchlistPage /></Require>} />
        <Route path="/review" element={<Require perm="view_review"><ReviewPage /></Require>} />
        <Route path="/screen" element={<StrategyScreen />} />
        <Route path="/news" element={<Require perm="view_news"><NewsPage /></Require>} />
        <Route path="/settings/notify" element={<Require perm="__admin__"><NotifyPage /></Require>} />
        <Route path="/settings/sources" element={<Require perm="data_sources"><DataSourcesPage /></Require>} />
        <Route path="/settings/llm" element={<Require perm="llm_settings"><LlmSettingsPage /></Require>} />
        <Route path="*" element={<StockAnalysis />} />
      </Route>
    </Routes>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <Root />
    </AuthProvider>
  )
}
