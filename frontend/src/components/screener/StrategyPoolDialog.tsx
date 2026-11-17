/**
 * 策略池管理弹窗 - 参考 tickflow 设计
 *
 * 功能：
 * - 策略池增删排序
 * - 策略分类浏览（全部/内置/自定义/AI）
 * - 批量添加/清空
 * - 拖拽排序（简化版）
 * - 导入策略文件
 */

import { useState, useMemo, useCallback, useRef } from 'react'
import { X, Plus, Trash2, GripVertical, Package, Upload } from 'lucide-react'
import type { StrategyMeta } from '../lib/types'

interface StrategyPoolDialogProps {
  open: boolean
  onClose: () => void
  strategies: StrategyMeta[]
  pool: string[]
  onConfirm: (pool: string[]) => void
}

type TabType = 'all' | 'builtin' | 'custom' | 'ai'

export function StrategyPoolDialog({
  open, onClose, strategies, pool, onConfirm
}: StrategyPoolDialogProps) {
  const [draftPool, setDraftPool] = useState<string[]>(pool)
  const [tab, setTab] = useState<TabType>('all')
  const [search, setSearch] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  // 策略映射
  const stratMap = useMemo(() => {
    const map = new Map<string, StrategyMeta>()
    for (const s of strategies) map.set(s.id, s)
    return map
  }, [strategies])

  // 有效已选
  const validDraft = useMemo(() => draftPool.filter(id => stratMap.has(id)), [draftPool, stratMap])

  // 可选策略（排除已选）
  const available = useMemo(() => {
    return strategies.filter(s => !draftPool.includes(s.id))
  }, [strategies, draftPool])

  // 按 Tab 过滤
  const filteredAvailable = useMemo(() => {
    let list = available
    if (tab === 'builtin') list = list.filter(s => !s.id.startsWith('custom_') && !s.id.startsWith('ai_'))
    if (tab === 'custom') list = list.filter(s => s.id.startsWith('custom_'))
    if (tab === 'ai') list = list.filter(s => s.id.startsWith('ai_'))
    if (search) {
      const q = search.toLowerCase()
      list = list.filter(s => s.name.toLowerCase().includes(q) || s.id.toLowerCase().includes(q))
    }
    return list
  }, [available, tab, search])

  // Tab 计数
  const tabCounts = useMemo(() => ({
    all: available.length,
    builtin: available.filter(s => !s.id.startsWith('custom_') && !s.id.startsWith('ai_')).length,
    custom: available.filter(s => s.id.startsWith('custom_')).length,
    ai: available.filter(s => s.id.startsWith('ai_')).length,
  }), [available])

  const handleAdd = useCallback((id: string) => {
    setDraftPool(prev => [...prev, id])
  }, [])

  const handleRemove = useCallback((id: string) => {
    setDraftPool(prev => prev.filter(x => x !== id))
  }, [])

  const handleClearAll = useCallback(() => {
    setDraftPool([])
  }, [])

  const handleAddAll = useCallback(() => {
    setDraftPool(prev => [...new Set([...prev, ...filteredAvailable.map(s => s.id)])])
  }, [filteredAvailable])

  // 导入策略文件
  const handleImportFile = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (ev) => {
      const content = ev.target?.result as string
      // 提取策略 ID
      const idMatch = content.match(/id\s*=\s*["']([^"']+)["']/)
      const id = idMatch ? idMatch[1] : `custom_${Date.now().toString(36)}`
      // 添加到池中
      if (!draftPool.includes(id)) {
        setDraftPool(prev => [...prev, id])
      }
    }
    reader.readAsText(file)
    e.target.value = '' // 清空 input
  }, [draftPool])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="mx-4 w-full max-w-4xl rounded-xl border border-surface-700 bg-surface-850 shadow-xl">
        {/* 头部 */}
        <div className="flex items-center justify-between border-b border-surface-700 px-4 py-3">
          <div className="flex items-center gap-3">
            <Package className="h-5 w-5 text-brand" />
            <h2 className="text-sm font-semibold text-slate-100">策略池</h2>
            <span className="text-[11px] text-slate-400">
              {validDraft.length} / {strategies.length}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <button onClick={() => fileInputRef.current?.click()}
              className="flex items-center gap-1 rounded-lg border border-surface-600 px-3 py-1.5 text-xs text-slate-300 hover:bg-surface-700">
              <Upload className="h-3.5 w-3.5" />
              导入策略
            </button>
            <input ref={fileInputRef} type="file" accept=".py" onChange={handleImportFile} className="hidden" />
            <button onClick={onClose} className="text-slate-400 hover:text-slate-200">
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* 内容区 */}
        <div className="grid grid-cols-2 gap-4 p-4">
          {/* 左栏：待选策略 */}
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <div className="flex gap-1 rounded-lg bg-surface-800 p-1">
                {(['all', 'builtin', 'custom', 'ai'] as TabType[]).map(t => (
                  <button key={t} onClick={() => setTab(t)}
                    className={`rounded px-2 py-1 text-[11px] ${
                      tab === t ? 'bg-brand/20 text-brand' : 'text-slate-400 hover:text-slate-200'
                    }`}>
                    {t === 'all' ? '全部' : t === 'builtin' ? '内置' : t === 'custom' ? '自定义' : 'AI'}
                    <span className="ml-1 text-[10px]">{tabCounts[t]}</span>
                  </button>
                ))}
              </div>
              <button onClick={handleAddAll}
                className="ml-auto rounded border border-surface-600 px-2 py-1 text-[11px] text-slate-400 hover:bg-surface-700 hover:text-slate-200">
                本组全加
              </button>
            </div>

            <input type="text" value={search} onChange={e => setSearch(e.target.value)}
              placeholder="搜索策略..." className="w-full rounded-lg border border-surface-600 bg-surface-800 px-3 py-1.5 text-xs text-slate-200 placeholder-slate-500" />

            <div className="max-h-[400px] space-y-1.5 overflow-y-auto">
              {filteredAvailable.length === 0 ? (
                <div className="py-4 text-center text-[11px] text-slate-500">无可用策略</div>
              ) : (
                filteredAvailable.map(s => (
                  <div key={s.id}
                    onClick={() => handleAdd(s.id)}
                    className="flex cursor-pointer items-center justify-between rounded-lg bg-surface-800 px-3 py-2 transition-colors hover:bg-surface-700">
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-slate-200">{s.name}</span>
                        <span className="rounded bg-surface-700 px-1.5 py-0.5 text-[10px] text-slate-400">
                          {s.id}
                        </span>
                      </div>
                      <div className="mt-0.5 text-[10px] text-slate-500 line-clamp-1">{s.description}</div>
                    </div>
                    <Plus className="h-4 w-4 text-slate-500" />
                  </div>
                ))
              )}
            </div>
          </div>

          {/* 右栏：已选策略 */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-400">已选：上下拖拽排序</span>
              {validDraft.length > 0 && (
                <button onClick={handleClearAll}
                  className="flex items-center gap-1 text-[11px] text-rose-400 hover:text-rose-300">
                  <Trash2 className="h-3.5 w-3.5" />
                  清空
                </button>
              )}
            </div>

            <div className="max-h-[400px] space-y-1.5 overflow-y-auto">
              {validDraft.length === 0 ? (
                <div className="py-4 text-center text-[11px] text-slate-500">点击左侧策略添加到池中</div>
              ) : (
                validDraft.map((id, index) => {
                  const s = stratMap.get(id)
                  return (
                    <div key={id}
                      className="flex items-center justify-between rounded-lg bg-surface-800 px-3 py-2">
                      <div className="flex items-center gap-3">
                        <GripVertical className="h-4 w-4 text-slate-600 cursor-grab" />
                        <div className="flex-1">
                          <div className="flex items-center gap-2">
                            <span className="text-xs text-slate-200">{s?.name ?? id}</span>
                            <span className="rounded bg-surface-700 px-1.5 py-0.5 text-[10px] text-slate-400">
                              {id}
                            </span>
                            <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] text-emerald-400">
                              内置
                            </span>
                          </div>
                        </div>
                      </div>
                      <button onClick={() => handleRemove(id)}
                        className="text-slate-500 hover:text-rose-400">
                        <X className="h-4 w-4" />
                      </button>
                    </div>
                  )
                })
              )}
            </div>
          </div>
        </div>

        {/* 底部 */}
        <div className="flex items-center justify-between border-t border-surface-700 px-4 py-3">
          <span className="text-[11px] text-slate-500">
            仅策略池中的策略会在扫描时运行
          </span>
          <div className="flex gap-2">
            <button onClick={onClose}
              className="rounded-lg border border-surface-600 px-4 py-1.5 text-xs text-slate-300 hover:bg-surface-700">
              取消
            </button>
            <button onClick={() => { onConfirm(draftPool); onClose() }}
              className="rounded-lg bg-brand px-4 py-1.5 text-xs text-white hover:bg-brand/80">
              确定
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
