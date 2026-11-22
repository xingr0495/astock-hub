import { Fragment, useEffect, useState, useRef } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { Menu, X, Pencil, RotateCcw, ChevronUp, ChevronDown, Eye, EyeOff, Sun, Moon } from 'lucide-react'
import { toggleTheme, useTheme } from '../lib/theme'
import ChatWidget from './ChatWidget'
import QuickOpen from './QuickOpen'
import { ToastContainer } from './Toast'
import { useAuth } from '../auth'

// 轻量分组标题（A2-6）：仅展示分组，不影响现有排序/隐藏逻辑
const GROUP_OF: Record<string, string> = {
  '/': '看盘', '/dashboard': '看盘', '/market-overview': '看盘',
  '/sector': '看盘', '/review': '看盘',
  '/watchlist': '选股', '/screen': '选股', '/pool': '选股',
  '/backtest': '交易与回测', '/trading': '交易与回测', '/paper': '交易与回测',
  '/news': '资讯', '/settings': '设置',
}

// perm/admin 可选字段（规格 §1 权限模型）：无字段 = view_market 默认档可见；
// perm 走普通权限键（管理员直通），admin: true 仅管理员。
const NAV: { to: string; label: string; perm?: string; admin?: boolean;
             feature?: string; featureAny?: string[] }[] = [
  { to: '/', label: '个股分析' },
  { to: '/watchlist', label: '自选股', perm: 'watchlist' },
  { to: '/dashboard', label: '实时看板' },
  { to: '/market-overview', label: '市场总览' },
  { to: '/sector', label: '板块分析' },
  { to: '/review', label: '大盘复盘', perm: 'view_review' },
  { to: '/screen', label: '策略选股' },
  { to: '/news', label: '产业资讯', perm: 'view_news' },
  { to: '/settings/notify', label: '推送设置', admin: true },
  { to: '/settings/sources', label: '数据源', perm: 'data_sources' },
  { to: '/settings/llm', label: '大模型', perm: 'llm_settings' },
]

// 侧栏自定义：服务端 /api/nav-config 为权威（手机/电脑同步），localStorage 为本机兜底与迁移源
const ORDER_KEY = 'astock_nav_order'
const HIDDEN_KEY = 'astock_nav_hidden'

async function fetchNavConfig(): Promise<{ order: string[]; hidden: string[] } | null> {
  try {
    const r = await fetch('/api/nav-config')
    if (!r.ok) return null
    const d = await r.json()
    return Array.isArray(d.order) && Array.isArray(d.hidden) ? d : null
  } catch {
    return null
  }
}

function putNavConfig(order: string[], hidden: string[]) {
  // 火即忘：服务端不可用时静默失败，localStorage 仍保证本机不丢
  fetch('/api/nav-config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ order, hidden }),
  }).catch(() => {})
}

function loadKeys(key: string): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(key) || '[]')
    return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === 'string') : []
  } catch {
    return []
  }
}

/** 合并持久化顺序与 NAV：旧顺序保留，新增栏目自动追加尾部（代码演进不丢项） */
function mergeOrder(saved: string[]): string[] {
  const valid = saved.filter((k) => NAV.some((n) => n.to === k))
  const rest = NAV.map((n) => n.to).filter((k) => !valid.includes(k))
  return [...valid, ...rest]
}

function linkClass(isActive: boolean): string {
  return `rounded-md px-3 py-2.5 text-sm transition-colors md:py-2 ${
    isActive
      ? 'bg-brand/15 text-brand'
      : 'text-slate-400 hover:bg-surface-700 hover:text-slate-200'
  }`
}

function ThemeToggle() {
  const theme = useTheme()
  const dark = theme === 'dark'
  return (
    <button
      onClick={() => toggleTheme()}
      aria-label={dark ? '切换到日间模式' : '切换到夜间模式'}
      title={dark ? '切换到日间模式' : '切换到夜间模式'}
      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-surface-700 hover:text-slate-200"
    >
      {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </button>
  )
}

export default function Layout() {
  const { can, isAdmin, features } = useAuth()
  const [drawer, setDrawer] = useState(false)
  const [navOrder, setNavOrder] = useState<string[]>(() => mergeOrder(loadKeys(ORDER_KEY)))
  const [navHidden, setNavHidden] = useState<Set<string>>(() => new Set(loadKeys(HIDDEN_KEY)))
  const [editNav, setEditNav] = useState(false)
  const loadedRef = useRef(false)
  const dirtyRef = useRef(false)

  // 挂载时拉服务端配置：服务端为准；服务端为空而本机有旧数据 → 迁移上云；
  // 本机已先动手改过（dirty）则不覆盖。失败静默（后续保存仍可恢复服务端）。
  useEffect(() => {
    let cancelled = false
    fetchNavConfig().then((srv) => {
      if (cancelled || !srv) { loadedRef.current = true; return }
      if (!dirtyRef.current) {
        if (srv.order.length > 0 || srv.hidden.length > 0) {
          setNavOrder(mergeOrder(srv.order))
          setNavHidden(new Set(srv.hidden))
        } else {
          const localOrder = loadKeys(ORDER_KEY)
          const localHidden = loadKeys(HIDDEN_KEY)
          // 服务端权威为全局单档、写接口仅管理员（规格 §1 导航写仅管理员）：非管理员不迁移上云，避免 PUT 403
          if (isAdmin && (localOrder.length || localHidden.length)) putNavConfig(localOrder, localHidden)
        }
      }
      loadedRef.current = true
    })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!loadedRef.current) return  // 服务端状态未定前不回写，防覆盖
    dirtyRef.current = true
    localStorage.setItem(ORDER_KEY, JSON.stringify(navOrder))
    localStorage.setItem(HIDDEN_KEY, JSON.stringify([...navHidden]))
    // 导航写仅管理员：非管理员跳过服务端回写（否则每次挂载同步都产生 PUT 403 噪音），localStorage 照写不丢
    if (isAdmin) putNavConfig(navOrder, [...navHidden])
  }, [navOrder, navHidden, isAdmin])

  const orderedNav = navOrder
    .map((k) => NAV.find((n) => n.to === k))
    .filter((n): n is (typeof NAV)[number] => !!n)
  // 权限过滤（规格 §1）：admin 项仅管理员；perm 项管理员直通、普通用户按 perms
  const navVisible = (n: (typeof NAV)[number]) =>
    (!n.admin || isAdmin) && (!n.perm || isAdmin || can(n.perm)) &&
    (!n.feature || !!features[n.feature]) &&
    (!n.featureAny || n.featureAny.some((f) => !!features[f]))
  const visibleNav = orderedNav.filter((n) => navVisible(n) && !navHidden.has(n.to))
  // 编辑模式仅管理员可进（按钮对非管理员不渲染，双保险：状态置真也不进编辑态）
  const editing = editNav && isAdmin
  const renderNav = editing ? orderedNav.filter(navVisible) : visibleNav

  function move(key: string, dir: -1 | 1) {
    setNavOrder((prev) => {
      const i = prev.indexOf(key)
      const j = i + dir
      if (i < 0 || j < 0 || j >= prev.length) return prev
      const next = [...prev]
      ;[next[i], next[j]] = [next[j], next[i]]
      return next
    })
  }

  function toggleHidden(key: string) {
    setNavHidden((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  function resetNav() {
    setNavOrder(NAV.map((n) => n.to))
    setNavHidden(new Set())
  }

  /** 侧栏条目：编辑态显示上下移动 + 显隐按钮，普通态是链接（手机抽屉点击后收起） */
  const renderItems = (onNavigate?: () => void) => {
    let lastGroup = ''
    return renderNav.map(({ to, label }) => {
      const group = GROUP_OF[to] ?? ''
      const header = group && group !== lastGroup ? (
        <div key={`g-${group}`} className="px-2 pb-1 pt-3 text-[10px] font-medium uppercase tracking-wider text-slate-500">
          {group}
        </div>
      ) : null
      lastGroup = group || lastGroup
      const idx = orderedNav.findIndex((n) => n.to === to)
      const hidden = navHidden.has(to)
      if (editing) {
        return (<Fragment key={to}>{header}
          <div
            key={to}
            className={`flex items-center gap-1 rounded-md px-2 py-1.5 text-sm md:py-1 ${
              hidden ? 'text-slate-600' : 'text-slate-300'
            } ${hidden && !onNavigate ? 'bg-surface-800/50' : 'bg-surface-800'}`}
          >
            <span className={`min-w-0 flex-1 truncate ${hidden ? 'line-through' : ''}`}>{label}</span>
            <button
              onClick={() => move(to, -1)}
              disabled={idx === 0}
              aria-label={`上移${label}`}
              className="rounded p-1 text-slate-400 hover:bg-surface-700 hover:text-slate-200 disabled:opacity-30"
            >
              <ChevronUp className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={() => move(to, 1)}
              disabled={idx === orderedNav.length - 1}
              aria-label={`下移${label}`}
              className="rounded p-1 text-slate-400 hover:bg-surface-700 hover:text-slate-200 disabled:opacity-30"
            >
              <ChevronDown className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={() => toggleHidden(to)}
              aria-label={`${hidden ? '显示' : '隐藏'}${label}`}
              className="rounded p-1 text-slate-400 hover:bg-surface-700 hover:text-slate-200"
            >
              {hidden ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
            </button>
          </div>
        </Fragment>)
      }
      return (<Fragment key={to}>{header}
        <NavLink
          key={to}
          to={to}
          end={to === '/'}
          onClick={onNavigate}
          className={({ isActive }) => linkClass(isActive)}
        >
          {label}
        </NavLink>
      </Fragment>)
    })
  }

  const editBar = (
    <div className="flex items-center gap-1 px-2 pb-1">
      <button
        onClick={resetNav}
        title="恢复默认顺序与显隐"
        className="flex items-center gap-1 rounded px-2 py-1 text-[11px] text-slate-400 hover:bg-surface-700 hover:text-slate-200"
      >
        <RotateCcw className="h-3 w-3" />
        恢复默认
      </button>
      <button
        onClick={() => setEditNav(false)}
        className="ml-auto rounded bg-brand/20 px-2 py-1 text-[11px] text-brand hover:bg-brand/30"
      >
        完成
      </button>
    </div>
  )

  // 底部用户条：机主单用户，无退出登录；保留主题切换。
  const userBar = (
    <div className="mb-2 flex items-center justify-between gap-1 rounded-md bg-surface-800 px-2 py-1.5">
      <span className="min-w-0 flex-1 truncate text-[11px] text-slate-300" title="机主（单用户）">
        👑 机主
      </span>
      <ThemeToggle />
    </div>
  )

  return (
    <div className="flex h-full">
      {/* 桌面侧边栏（≥768px 原样保留） */}
      <aside className="hidden w-44 shrink-0 flex-col border-r border-surface-700 bg-surface-850 md:flex">
        <div className="flex items-center gap-2 px-4 py-5">
          <img src="/mind-ashare-logo.png" alt="Mind AShare Logo" className="h-9 w-9 rounded-lg object-cover" />
          <div>
            <div className="text-sm font-semibold text-slate-100">Mind AShare</div>
            <div className="text-[11px] text-slate-400">A股五维投研</div>
            <div className="mt-1 text-[9px] leading-relaxed text-slate-500">Mind-Astock: Your AI Co-Pilot for A-Share Alpha</div>
          </div>
        </div>
        <nav className="mt-2 flex flex-col gap-1 px-2">
          {renderItems()}
        </nav>
        <div className="mt-auto px-2 pb-4">
          {userBar}
          {editing ? (
            editBar
          ) : isAdmin && (
            <button
              onClick={() => setEditNav(true)}
              title="自定义栏目顺序与显隐"
              className="flex w-full items-center gap-1.5 rounded-md px-3 py-2 text-[11px] text-slate-500 transition-colors hover:bg-surface-700 hover:text-slate-300"
            >
              <Pencil className="h-3 w-3" />
              自定义栏目
            </button>
          )}
          <div className="mt-2 px-1 text-[11px] leading-relaxed text-slate-500">
            五维交叉验证
            <br />
            单维度=参考 · 三维共振=行动
            <br />
            行情仅供参考
          </div>
        </div>
      </aside>

      {/* 手机顶栏（<768px） */}
      <div className="fixed inset-x-0 top-0 z-40 flex h-12 items-center gap-2 border-b border-surface-700 bg-surface-850 px-3 md:hidden">
        <button
          onClick={() => setDrawer(true)}
          aria-label="打开菜单"
          className="-ml-1 flex h-10 w-10 items-center justify-center rounded-md text-slate-300 active:bg-surface-700"
        >
          <Menu className="h-5 w-5" />
        </button>
        <span className="flex items-center gap-1.5 text-sm font-semibold text-slate-100"><img src="/mind-ashare-logo.png" alt="" className="h-5 w-5 rounded object-cover" />Mind AShare</span>
        <span className="text-[11px] text-slate-500">A股五维投研</span>
        <div className="ml-auto"><ThemeToggle /></div>
      </div>

      {/* 手机侧滑抽屉（<768px） */}
      {drawer && (
        <div className="fixed inset-0 z-50 md:hidden" onClick={() => setDrawer(false)}>
          <div className="absolute inset-0 bg-black/60" />
          <div
            className="absolute inset-y-0 left-0 flex w-56 flex-col border-r border-surface-700 bg-surface-850"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 py-4">
              <div>
                <span className="flex items-center gap-1.5 text-sm font-semibold text-slate-100"><img src="/mind-ashare-logo.png" alt="" className="h-5 w-5 rounded object-cover" />Mind AShare</span>
                <div className="mt-1 max-w-40 text-[9px] leading-relaxed text-slate-500">Mind-Astock: Your AI Co-Pilot for A-Share Alpha</div>
              </div>
              <button
                onClick={() => setDrawer(false)}
                aria-label="关闭菜单"
                className="flex h-10 w-10 items-center justify-center rounded-md text-slate-400 active:bg-surface-700"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <nav className="flex flex-1 flex-col gap-1 overflow-y-auto px-2 pb-4">
              {renderItems(() => setDrawer(false))}
            </nav>
            <div className="px-2 pb-4">
              {userBar}
              {editing ? (
                editBar
              ) : isAdmin && (
                <button
                  onClick={() => setEditNav(true)}
                  title="自定义栏目顺序与显隐"
                  className="flex w-full items-center gap-1.5 rounded-md px-3 py-2.5 text-[11px] text-slate-500 active:bg-surface-700"
                >
                  <Pencil className="h-3.5 w-3.5" />
                  自定义栏目
                </button>
              )}
              <div className="mt-2 px-1 text-[11px] leading-relaxed text-slate-500">
                单维度=参考 · 三维共振=行动
                <br />
                行情仅供参考
              </div>
            </div>
          </div>
        </div>
      )}

      <main className="min-w-0 flex-1 overflow-y-auto pt-12 md:pt-0">
        <div className="mx-auto max-w-[1400px] p-4 md:p-6">
          <Outlet />
        </div>
      </main>

      {/* AI 对话悬浮框：fixed 定位不进文档流，挂在 main 之后 → 所有路由页面可用；权限闸=管理员或 ai_chat */}
      {(isAdmin || can('ai_chat')) && (<><QuickOpen /><ChatWidget /></>)}
      {/* 全局 toast 容器（模拟盘加股等轻反馈；fixed 右下角不进文档流） */}
      <ToastContainer />
    </div>
  )
}
