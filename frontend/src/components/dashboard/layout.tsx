import { useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Eye, EyeOff, RotateCcw } from 'lucide-react'
import { getJson, postJson } from '../../lib/api'

/** 看板区块目录（与后端 astock_hub/dashboard_layout.py 一一对应）。 */
export const SECTIONS = [
  { id: 'indices', label: '指数条' },
  { id: 'kpi', label: 'KPI 六格' },
  { id: 'trendband', label: '趋势带' },
  { id: 'global', label: '美股/港股' },
  { id: 'main', label: '主体区（分布/情绪/主线/榜单/梯队）' },
  { id: 'bottom', label: '底部卡片区（资金/纪要/资讯/国家队）' },
  { id: 'watchbar', label: '我的关注' },
] as const

export type Layout = { order: string[]; hidden: string[] }
export const DEFAULT_LAYOUT: Layout = { order: SECTIONS.map((s) => s.id), hidden: [] }

export function useDashboardLayout() {
  return useQuery<Layout>({
    queryKey: ['dashboard-layout'],
    queryFn: () => getJson<Layout>('/api/dashboard-layout'),
    staleTime: 60_000,
  })
}

/** 区块包装：按用户布局显隐 + CSS order 排序（默认布局时与历史顺序一致）。 */
export function DashSection({ id, layout, children }: {
  id: string
  layout: Layout
  children: ReactNode
}) {
  if (layout.hidden.includes(id)) return null
  const idx = layout.order.indexOf(id)
  return <div style={{ order: idx >= 0 ? idx : 99 }}>{children}</div>
}

/** 编辑面板：↑↓ 调序、👁 显隐、恢复默认；保存后全员生效（仅管理员可编辑）。 */
export function DashboardLayoutEditor({ layout, onClose }: {
  layout: Layout
  onClose: () => void
}) {
  const qc = useQueryClient()
  const [order, setOrder] = useState<string[]>([...layout.order])
  const [hidden, setHidden] = useState<string[]>([...layout.hidden])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir
    if (j < 0 || j >= order.length) return
    const next = [...order]
    ;[next[i], next[j]] = [next[j], next[i]]
    setOrder(next)
  }
  const toggleHidden = (id: string) =>
    setHidden((h) => (h.includes(id) ? h.filter((x) => x !== id) : [...h, id]))

  const save = async (doc: Layout) => {
    setBusy(true)
    setMsg(null)
    try {
      await postJson('/api/dashboard-layout', doc)
      qc.invalidateQueries({ queryKey: ['dashboard-layout'] })
      onClose()
    } catch (e) {
      setMsg(`保存失败：${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="rounded-xl border border-brand/30 bg-surface-850 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-slate-100">自定义看板区块</h3>
        <span className="text-[11px] text-slate-500">↑↓ 调顺序，👁 显隐；保存后所有账号生效</span>
        <div className="ml-auto flex gap-2">
          <button
            onClick={() => save(DEFAULT_LAYOUT)}
            disabled={busy}
            className="inline-flex items-center gap-1 rounded-md border border-surface-600 px-2 py-1 text-[11px] text-slate-300 hover:text-slate-100 disabled:opacity-50"
          >
            <RotateCcw className="h-3 w-3" /> 恢复默认
          </button>
          <button
            onClick={() => save({ order, hidden })}
            disabled={busy}
            className="rounded-md bg-brand/20 px-3 py-1 text-[11px] text-brand hover:bg-brand/30 disabled:opacity-50"
          >
            {busy ? '保存中…' : '保存并完成'}
          </button>
          <button
            onClick={onClose}
            className="rounded-md px-2 py-1 text-[11px] text-slate-400 hover:text-slate-200"
          >
            取消
          </button>
        </div>
      </div>
      {msg && <div className="mb-2 text-[11px] text-rose-300">{msg}</div>}
      <div className="divide-y divide-surface-700/60">
        {order.map((id, i) => {
          const isHidden = hidden.includes(id)
          const label = SECTIONS.find((s) => s.id === id)?.label ?? id
          return (
            <div key={id} className="flex items-center gap-2 py-1.5">
              <button
                onClick={() => move(i, -1)}
                disabled={i === 0}
                className="rounded p-1 text-slate-400 hover:text-brand disabled:opacity-30"
                aria-label="上移"
              >
                <ArrowUp className="h-3.5 w-3.5" />
              </button>
              <button
                onClick={() => move(i, 1)}
                disabled={i === order.length - 1}
                className="rounded p-1 text-slate-400 hover:text-brand disabled:opacity-30"
                aria-label="下移"
              >
                <ArrowDown className="h-3.5 w-3.5" />
              </button>
              <span className={`text-[12px] ${isHidden ? 'text-slate-600 line-through' : 'text-slate-200'}`}>
                {label}
              </span>
              <button
                onClick={() => toggleHidden(id)}
                className={`ml-auto inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] ${
                  isHidden
                    ? 'border-surface-600 text-slate-500'
                    : 'border-brand/40 text-brand'
                }`}
              >
                {isHidden ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                {isHidden ? '已隐藏' : '显示中'}
              </button>
            </div>
          )
        })}
      </div>
    </section>
  )
}
