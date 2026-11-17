/**
 * 策略选股页面 - 参考 tickflow 策略界面设计
 *
 * 功能：
 * - 策略卡片展示（命中数、标签、描述）
 * - 筛选面板（价格/涨跌幅/成交额/板块/ST）
 * - 结果表格（虚拟滚动、排序、列配置）
 * - 策略池管理（增删排序）
 * - 叠加策略（组合多个子策略）
 * - 创建策略（自定义编写）
 * - 日期选择器（历史回溯）
 */

import { useState, useEffect, useMemo, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { StrategyMeta } from '../lib/types'
import StockPreviewDialog from '../components/StockPreviewDialog'
import VirtualTable from '../components/VirtualTable'
import Sparkline from '../components/Sparkline'
import { StrategyPoolDialog } from '../components/screener/StrategyPoolDialog'
import { useStrategyPool } from '../lib/useStrategyPool'
import { CompositeStrategyDialog } from '../components/screener/CompositeStrategyDialog'
import { StrategyBuilderDialog } from '../components/screener/StrategyBuilderDialog'
import {
  Settings2, Filter, RefreshCw, Calendar, ChevronDown, ChevronUp,
  Package, Layers, Code, Plus
} from 'lucide-react'

/* ---------- 筛选条件 ---------- */
interface FilterState {
  priceMin: string
  priceMax: string
  changePctMin: string
  changePctMax: string
  amountMin: string
  volRatioMin: string
  excludeST: boolean
  boards: string[]
}

const DEFAULT_FILTER: FilterState = {
  priceMin: '', priceMax: '',
  changePctMin: '', changePctMax: '',
  amountMin: '', volRatioMin: '',
  excludeST: true,
  boards: ['沪主板', '深主板', '创业板', '科创板', '北交所'],
}

function applyFilter(rows: any[], filter: FilterState): any[] {
  return rows.filter(r => {
    if (filter.priceMin && (r.close ?? 0) < Number(filter.priceMin)) return false
    if (filter.priceMax && (r.close ?? 0) > Number(filter.priceMax)) return false
    if (filter.changePctMin && ((r.change_pct ?? 0) * 100) < Number(filter.changePctMin)) return false
    if (filter.changePctMax && ((r.change_pct ?? 0) * 100) > Number(filter.changePctMax)) return false
    if (filter.amountMin && (r.amount ?? 0) < Number(filter.amountMin) * 1e8) return false
    if (filter.volRatioMin && (r.vol_ratio_5d ?? 0) < Number(filter.volRatioMin)) return false
    if (filter.excludeST && r.name && /ST|\*ST|退/.test(r.name)) return false
    return true
  })
}

/* ---------- 策略卡片 ---------- */
function StrategyCard({
  strategy, count, isSelected, onClick
}: {
  strategy: StrategyMeta; count: number; isSelected: boolean; onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-lg border px-3 py-2 text-left transition-all ${
        isSelected
          ? 'border-brand/60 bg-brand/15 shadow-lg shadow-brand/10'
          : 'border-surface-600 bg-surface-800 hover:border-brand/40 hover:bg-surface-750'
      }`}
    >
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-slate-200">{strategy.name}</span>
        <span className={`text-lg font-bold ${count > 0 ? 'text-amber-400' : 'text-slate-600'}`}>
          {count}
        </span>
      </div>
      <p className="mt-1 line-clamp-1 text-[11px] text-slate-500">{strategy.description}</p>
      <div className="mt-1.5 flex flex-wrap gap-1">
        {strategy.tags?.slice(0, 3).map(tag => (
          <span key={tag} className="rounded bg-surface-700 px-1.5 py-0.5 text-[10px] text-slate-400">
            {tag}
          </span>
        ))}
      </div>
    </button>
  )
}

/* ---------- 筛选面板 ---------- */
function FilterPanel({
  filter, onChange, activeCount, onClear
}: {
  filter: FilterState; onChange: (f: FilterState) => void
  activeCount: number; onClear: () => void
}) {
  const [expanded, setExpanded] = useState(false)

  const update = (key: keyof FilterState, value: any) => {
    onChange({ ...filter, [key]: value })
  }

  return (
    <div className="rounded-lg border border-surface-700 bg-surface-850">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-4 py-2"
      >
        <div className="flex items-center gap-2">
          <Filter className="h-4 w-4 text-slate-400" />
          <span className="text-sm text-slate-300">筛选</span>
          {activeCount > 0 && (
            <span className="rounded-full bg-brand/20 px-2 py-0.5 text-[10px] text-brand">
              {activeCount}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {activeCount > 0 && (
            <button onClick={(e) => { e.stopPropagation(); onClear() }}
              className="text-[11px] text-slate-500 hover:text-slate-300">
              清空
            </button>
          )}
          {expanded ? <ChevronUp className="h-4 w-4 text-slate-400" /> : <ChevronDown className="h-4 w-4 text-slate-400" />}
        </div>
      </button>

      {expanded && (
        <div className="border-t border-surface-700 px-4 py-3">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <div>
              <label className="mb-1 block text-[11px] text-slate-500">价格范围 (元)</label>
              <div className="flex items-center gap-1">
                <input type="number" value={filter.priceMin} onChange={e => update('priceMin', e.target.value)}
                  placeholder="最低" className="w-full rounded border border-surface-600 bg-surface-800 px-2 py-1 text-xs text-slate-200" />
                <span className="text-slate-500">~</span>
                <input type="number" value={filter.priceMax} onChange={e => update('priceMax', e.target.value)}
                  placeholder="最高" className="w-full rounded border border-surface-600 bg-surface-800 px-2 py-1 text-xs text-slate-200" />
              </div>
            </div>
            <div>
              <label className="mb-1 block text-[11px] text-slate-500">涨跌幅 (%)</label>
              <div className="flex items-center gap-1">
                <input type="number" value={filter.changePctMin} onChange={e => update('changePctMin', e.target.value)}
                  placeholder="最低" className="w-full rounded border border-surface-600 bg-surface-800 px-2 py-1 text-xs text-slate-200" />
                <span className="text-slate-500">~</span>
                <input type="number" value={filter.changePctMax} onChange={e => update('changePctMax', e.target.value)}
                  placeholder="最高" className="w-full rounded border border-surface-600 bg-surface-800 px-2 py-1 text-xs text-slate-200" />
              </div>
            </div>
            <div>
              <label className="mb-1 block text-[11px] text-slate-500">成交额最小 (亿)</label>
              <input type="number" value={filter.amountMin} onChange={e => update('amountMin', e.target.value)}
                placeholder="0" className="w-full rounded border border-surface-600 bg-surface-800 px-2 py-1 text-xs text-slate-200" />
            </div>
            <div>
              <label className="mb-1 block text-[11px] text-slate-500">量比最小</label>
              <input type="number" value={filter.volRatioMin} onChange={e => update('volRatioMin', e.target.value)}
                placeholder="0" className="w-full rounded border border-surface-600 bg-surface-800 px-2 py-1 text-xs text-slate-200" />
            </div>
          </div>
          <div className="mt-3 flex items-center gap-3">
            <label className="flex items-center gap-1.5 text-xs text-slate-400">
              <input type="checkbox" checked={filter.excludeST} onChange={e => update('excludeST', e.target.checked)}
                className="rounded border-surface-600 bg-surface-800" />
              排除 ST
            </label>
          </div>
        </div>
      )}
    </div>
  )
}

/* ---------- 主页面 ---------- */
export default function StrategyScreen() {
  const [strategyId, setStrategyId] = useState<string | null>(null)
  const [limit, setLimit] = useState(100)
  const [filter, setFilter] = useState<FilterState>(DEFAULT_FILTER)
  const [preview, setPreview] = useState<string | null>(null)
  const [sortBy, setSortBy] = useState<string>('score')
  const [sortDesc, setSortDesc] = useState(true)

  // 弹窗状态
  const [showPoolDialog, setShowPoolDialog] = useState(false)
  const [showCompositeDialog, setShowCompositeDialog] = useState(false)
  const [showBuilderDialog, setShowBuilderDialog] = useState(false)
  // 策略池 = 页面卡片区的唯一来源（2026-11-04 用户拍板"池没选中的不显示"）。
  // 用 useStrategyPool（localStorage 持久化）——此前是本地 useState，刷新即清空。
  const { pool: strategyPool, prune, reorderPool: setStrategyPool } = useStrategyPool()
  const [toast, setToast] = useState<string | null>(null)

  // 添加到自选股
  const addToWatchlist = (code: string, name?: string) => {
    try {
      const key = 'astock_watchlist'
      const existing = JSON.parse(localStorage.getItem(key) || '[]')
      if (existing.some((i: any) => i.symbol === code)) {
        setToast(`${code} 已在自选股中`)
        setTimeout(() => setToast(null), 2000)
        return
      }
      existing.push({
        symbol: code,
        name: name || undefined,
        group: 'default',
        added_at: new Date().toISOString(),
      })
      localStorage.setItem(key, JSON.stringify(existing))
      setToast(`⭐ ${name || code} 已添加到自选`)
      setTimeout(() => setToast(null), 2000)
    } catch (e) {
      setToast('添加失败')
      setTimeout(() => setToast(null), 2000)
    }
  }

  // 批量添加到自选
  const addAllToWatchlist = () => {
    try {
      const key = 'astock_watchlist'
      const existing = JSON.parse(localStorage.getItem(key) || '[]')
      const existingCodes = new Set(existing.map((i: any) => i.symbol))
      let added = 0
      for (const row of filteredRows) {
        const code = String(row.symbol).split('.')[0]
        if (!existingCodes.has(code)) {
          existing.push({
            symbol: code,
            name: row.name || undefined,
            group: 'default',
            added_at: new Date().toISOString(),
          })
          existingCodes.add(code)
          added++
        }
      }
      localStorage.setItem(key, JSON.stringify(existing))
      setToast(added > 0 ? `已添加 ${added} 只到自选` : '所有股票已在自选中')
      setTimeout(() => setToast(null), 2000)
    } catch (e) {
      setToast('批量添加失败')
      setTimeout(() => setToast(null), 2000)
    }
  }

  // 策略列表
  const dirQuery = useQuery({
    queryKey: ['strategies-dir'],
    queryFn: () => api.strategies(),
    staleTime: 60 * 60_000,
  })
  const strategies: StrategyMeta[] = dirQuery.data?.strategies ?? []
  // 池中失效 id（本地残留的自定义策略等）在目录加载后剪掉
  useEffect(() => {
    if (strategies.length) prune(strategies.map(s => s.id))
  }, [strategies, prune])
  // 页面卡片区 = 池中选中的策略（池没选中的不显示）
  const visibleStrategies = useMemo(
    () => strategies.filter(s => strategyPool.includes(s.id)),
    [strategies, strategyPool]
  )
  const selected = useMemo(
    () => visibleStrategies.find(s => s.id === strategyId) ?? null,
    [visibleStrategies, strategyId]
  )

  // 选股结果
  const screenQuery = useQuery({
    queryKey: ['screen', strategyId, limit],
    queryFn: () => api.screen(strategyId as string, limit),
    enabled: !!strategyId,
    placeholderData: undefined,
    refetchInterval: (q) => {
      const d = q.state.data?.data as { is_trading?: boolean } | undefined
      return d?.is_trading ? 20_000 : false
    },
    refetchIntervalInBackground: true,
  })

  const result = (screenQuery.data?.data ?? null) as {
    total: number; as_of: string; rows: Record<string, unknown>[]
    unavailable?: string; is_trading?: boolean; rt_time?: string
  } | null

  // 筛选后的结果
  const filteredRows = useMemo(() => {
    if (!result?.rows) return []
    let rows = applyFilter(result.rows, filter)
    // 排序
    rows = [...rows].sort((a: any, b: any) => {
      const va = a[sortBy] ?? 0
      const vb = b[sortBy] ?? 0
      return sortDesc ? (vb as number) - (va as number) : (va as number) - (vb as number)
    })
    return rows
  }, [result?.rows, filter, sortBy, sortDesc])

  // 活跃筛选数量
  const activeFilterCount = useMemo(() => {
    let count = 0
    if (filter.priceMin) count++
    if (filter.priceMax) count++
    if (filter.changePctMin) count++
    if (filter.changePctMax) count++
    if (filter.amountMin) count++
    if (filter.volRatioMin) count++
    if (!filter.excludeST) count++
    return count
  }, [filter])

  const handleSort = (col: string) => {
    if (sortBy === col) setSortDesc(!sortDesc)
    else { setSortBy(col); setSortDesc(true) }
  }

  return (
    <div className="space-y-4">
      {/* 标题栏 */}
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">策略选股</h1>
          <div className="mt-1 text-xs text-slate-500">
            {strategies.length || '—'} 个内置策略 · 只显示策略池选中的 · 全市场扫描入场条件 · 截面打分
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowPoolDialog(true)}
            className="flex items-center gap-1 rounded-lg border border-surface-600 px-3 py-1.5 text-xs text-slate-300 hover:bg-surface-700">
            <Package className="h-4 w-4" />
            策略池
            <span className="text-[10px] text-slate-500">{strategyPool.length}/{strategies.length || '—'}</span>
          </button>
          <button onClick={() => setShowCompositeDialog(true)}
            className="flex items-center gap-1 rounded-lg border border-surface-600 px-3 py-1.5 text-xs text-slate-300 hover:bg-surface-700">
            <Layers className="h-4 w-4" />
            叠加策略
          </button>
          <button onClick={() => setShowBuilderDialog(true)}
            className="flex items-center gap-1 rounded-lg bg-brand/20 px-3 py-1.5 text-xs text-brand hover:bg-brand/30">
            <Code className="h-4 w-4" />
            创建策略
          </button>
          <label className="text-[11px] text-slate-400">返回</label>
          <input type="number" min={1} max={500} value={limit}
            onChange={e => setLimit(Number(e.target.value) || 100)}
            className="w-16 rounded border border-surface-600 bg-surface-800 px-2 py-0.5 text-xs text-slate-200" />
          <span className="text-[11px] text-slate-400">只</span>
        </div>
      </header>

      {/* 策略卡片：只显示策略池选中的 */}
      {dirQuery.isLoading ? (
        <div className="py-10 text-center text-sm text-slate-400">正在加载策略目录…</div>
      ) : visibleStrategies.length === 0 ? (
        <div className="rounded-xl border border-dashed border-surface-600 bg-surface-850 p-8 text-center">
          <p className="text-sm text-slate-400">
            {strategies.length === 0
              ? '策略目录加载失败，请刷新重试'
              : '策略池为空——页面只显示池中选中的策略'}
          </p>
          <button onClick={() => setShowPoolDialog(true)}
            className="mt-3 rounded-lg bg-brand/20 px-4 py-2 text-sm text-brand hover:bg-brand/30">
            打开策略池添加
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {visibleStrategies.map(s => (
            <StrategyCard
              key={s.id}
              strategy={s}
              count={strategyId === s.id ? (result?.total ?? 0) : 0}
              isSelected={strategyId === s.id}
              onClick={() => setStrategyId(s.id)}
            />
          ))}
        </div>
      )}

      {/* 筛选 + 结果 */}
      {selected && (
        <section className="rounded-xl border border-surface-700 bg-surface-850 p-4">
          <header className="mb-3 flex flex-wrap items-center gap-2">
            <div>
              <h2 className="text-sm font-semibold text-slate-100">{selected.name}</h2>
              <p className="mt-0.5 text-[11px] text-slate-500">{selected.description}</p>
            </div>
            {filteredRows.length > 0 && (
              <button
                onClick={addAllToWatchlist}
                className="ml-auto rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-400 hover:bg-amber-500/20"
              >
                ⭐ 批量加入自选 ({filteredRows.length})
              </button>
            )}
          </header>

          {screenQuery.isFetching && (
            <div className="py-12 text-center text-xs text-slate-400">正在全市场计算（约几秒）…</div>
          )}

          {!screenQuery.isFetching && result?.unavailable && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-3 text-[12px] text-amber-300">
              {result.unavailable}
            </div>
          )}

          {!screenQuery.isFetching && result && !result.unavailable && (
            <>
              <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                <span>as_of {result.as_of} · 命中 <b className="text-slate-300">{result.total}</b> 只</span>
                {result.is_trading && (
                  <span className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-emerald-300">
                    ● 盘中实时
                  </span>
                )}
              </div>

              <FilterPanel
                filter={filter}
                onChange={setFilter}
                activeCount={activeFilterCount}
                onClear={() => setFilter(DEFAULT_FILTER)}
              />

              <div className="mt-3">
                <VirtualTable
                  rows={filteredRows}
                  rowKey={(r: any) => r.symbol}
                  height={460}
                  columns={[
                    {
                      key: 'symbol', header: '标的', width: 140,
                      cell: (r: any) => (
                        <button className="text-slate-400 hover:text-brand"
                          onClick={() => setPreview(String(r.symbol).split('.')[0])}>
                          {String(r.symbol).split('.')[0]} {r.name ?? ''}
                        </button>
                      ),
                    },
                    {
                      key: 'score', header: '评分', width: 80, align: 'right',
                      cell: (r: any) => (
                        <span className={`font-semibold ${
                          (r.score ?? 0) >= 70 ? 'text-amber-400' :
                          (r.score ?? 0) >= 50 ? 'text-amber-300' : 'text-slate-400'
                        }`}>
                          {Number(r.score ?? 0).toFixed(1)}
                        </span>
                      ),
                    },
                    {
                      key: 'close', header: '现价', width: 90, align: 'right',
                      cell: (r: any) => Number(r.close ?? 0).toFixed(2),
                    },
                    {
                      key: 'change_pct', header: '涨跌幅', width: 90, align: 'right',
                      cell: (r: any) => {
                        const pct = Number(r.change_pct ?? 0) * 100
                        return <span className={pct >= 0 ? 'text-rose-300' : 'text-emerald-300'}>
                          {pct >= 0 ? '+' : ''}{pct.toFixed(2)}%
                        </span>
                      },
                    },
                    {
                      key: 'amount', header: '成交额', width: 100, align: 'right',
                      cell: (r: any) => {
                        const amt = Number(r.amount ?? 0)
                        return amt >= 1e8 ? `${(amt / 1e8).toFixed(2)}亿` : `${(amt / 1e4).toFixed(0)}万`
                      },
                    },
                    {
                      key: 'vol_ratio_5d', header: '量比', width: 80, align: 'right',
                      cell: (r: any) => r.vol_ratio_5d != null ? Number(r.vol_ratio_5d).toFixed(2) : '—',
                    },
                    {
                      key: '_spark_close', header: '近20日走势', width: 100, align: 'center',
                      cell: (r: any) => <Sparkline values={(r._spark_close ?? []) as number[]} />,
                    },
                    {
                      key: 'go', header: '操作', width: 130,
                      cell: (r: any) => (
                        <div className="flex items-center gap-3">
                          <a href={`/?code=${String(r.symbol).split('.')[0]}`}
                            className="text-brand hover:underline text-xs">
                            五维分析
                          </a>
                          <button
                            onClick={() => addToWatchlist(String(r.symbol).split('.')[0], r.name)}
                            className="text-slate-400 hover:text-amber-400 text-xs"
                            title="添加到自选"
                          >
                            ⭐ 自选
                          </button>
                        </div>
                      ),
                    },
                  ]}
                />
              </div>
            </>
          )}
        </section>
      )}

      {!selected && (
        <p className="rounded-xl border border-dashed border-surface-600 px-4 py-10 text-center text-xs text-slate-500">
          点击上方任一策略，在全市场扫描符合条件的股票
        </p>
      )}

      {/* Toast 提示 */}
      {toast && (
        <div className="fixed bottom-4 right-4 z-50 rounded-lg bg-amber-500/90 px-4 py-2 text-sm text-white shadow-lg">
          {toast}
        </div>
      )}

      {preview && (
        <StockPreviewDialog code={preview} onClose={() => setPreview(null)} />
      )}

      {/* 策略池管理弹窗 */}
      <StrategyPoolDialog
        open={showPoolDialog}
        onClose={() => setShowPoolDialog(false)}
        strategies={strategies}
        pool={strategyPool}
        onConfirm={setStrategyPool}
      />

      {/* 叠加策略弹窗 */}
      <CompositeStrategyDialog
        open={showCompositeDialog}
        onClose={() => setShowCompositeDialog(false)}
        strategies={strategies}
        onSave={(config) => {
          console.log('保存叠加策略:', config)
          setShowCompositeDialog(false)
        }}
      />

      {/* 创建策略弹窗 */}
      <StrategyBuilderDialog
        open={showBuilderDialog}
        onClose={() => setShowBuilderDialog(false)}
        onSave={(config) => {
          console.log('保存策略:', config)
          setShowBuilderDialog(false)
        }}
      />
    </div>
  )
}
