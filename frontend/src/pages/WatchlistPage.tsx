/**
 * 自选股页面
 *
 * 功能：
 * - 自选股列表（实时行情：现价/涨跌幅/涨跌额/成交额）
 * - 添加/删除自选股
 * - 自定义分组管理（新建/删除/行内换组）
 * - 点击跳转个股五维分析
 * - 排序（按涨跌幅/代码）
 *
 * 存储：服务端 data/watchlist.json（跨浏览器/设备同步）；
 * 首次打开时自动把浏览器 localStorage 旧数据迁移到服务端。
 */

import { useState, useMemo, useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { Star, Trash2, Plus, Search, ArrowUpDown } from 'lucide-react'

// 旧版 localStorage（仅用于一次性迁移）
const STORAGE_KEY = 'astock_watchlist'
const GROUPS_KEY = 'astock_watchlist_groups'

interface WatchItem {
  symbol: string
  name?: string
  group: string
  added_at: string
}

interface WatchGroup {
  id: string
  name: string
}

function readLegacy(): { items: WatchItem[]; groups: WatchGroup[] } {
  try {
    const items = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    const groups = JSON.parse(localStorage.getItem(GROUPS_KEY) || '[]')
    return { items, groups }
  } catch {
    return { items: [], groups: [] }
  }
}

function clearLegacy() {
  localStorage.removeItem(STORAGE_KEY)
  localStorage.removeItem(GROUPS_KEY)
}

// 通过代码查询股票名称
async function fetchStockName(code: string): Promise<string> {
  try {
    const res = await fetch(`/api/symbol-search?q=${code}&limit=1`)
    const data = await res.json()
    if (data.items && data.items.length > 0) {
      return data.items[0].name || ''
    }
  } catch {}
  return ''
}

export default function WatchlistPage() {
  const qc = useQueryClient()
  const [activeGroup, setActiveGroup] = useState<string>('all')
  const [search, setSearch] = useState('')
  const [addCode, setAddCode] = useState('')
  const [addName, setAddName] = useState('')
  const [addGroup, setAddGroup] = useState('default')
  const [showAdd, setShowAdd] = useState(false)
  const [sortBy, setSortBy] = useState<'change' | 'code'>('change')
  const [nameLoading, setNameLoading] = useState(false)
  const [newGroupName, setNewGroupName] = useState('')
  const [showNewGroup, setShowNewGroup] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [batchDeleting, setBatchDeleting] = useState(false)
  const migratedRef = useRef(false)

  // 服务端自选股状态
  const stateQuery = useQuery({
    queryKey: ['watchlist-server'],
    queryFn: () => api.watchlist(),
    staleTime: 10_000,
  })
  const items: WatchItem[] = stateQuery.data?.items ?? []
  const groups: WatchGroup[] = stateQuery.data?.groups ?? [{ id: 'default', name: '默认分组' }]

  // 一次性迁移：服务端为空且浏览器有旧数据时，导入服务端
  useEffect(() => {
    if (migratedRef.current || !stateQuery.data) return
    migratedRef.current = true
    const legacy = readLegacy()
    if (stateQuery.data.items.length === 0 && legacy.items.length > 0) {
      api.watchlistImport(legacy.items, legacy.groups).then((state) => {
        qc.setQueryData(['watchlist-server'], state)
        clearLegacy()
      }).catch(() => { /* 迁移失败不清旧数据，下次再试 */ })
    } else if (stateQuery.data.items.length > 0 && legacy.items.length > 0) {
      clearLegacy() // 服务端已有数据，旧本地数据退役
    }
  }, [stateQuery.data, qc])

  function applyState(state: any) {
    if (state && state.items && state.groups) {
      qc.setQueryData(['watchlist-server'], state)
      setError(null)
    } else if (state && state.error) {
      setError(state.error)
    }
  }

  // 输入代码后自动查询股票名称
  useEffect(() => {
    const code = addCode.trim().replace(/\D/g, '').slice(0, 6)
    if (code.length === 6 && !addName) {
      setNameLoading(true)
      fetchStockName(code).then(name => {
        if (name) {
          setAddName(name)
        }
        setNameLoading(false)
      }).catch(() => setNameLoading(false))
    }
  }, [addCode])

  // 获取自选股行情（传裸 6 位代码，适配器自行补市场前缀）
  const symbols = items.map(i => i.symbol)
  const quoteQuery = useQuery({
    queryKey: ['watchlist-quotes', symbols.join(',')],
    queryFn: () => api.quote(symbols.join(',')),
    enabled: symbols.length > 0,
    staleTime: 5_000,
    refetchInterval: 10_000,
  })

  // 解析行情数据（key 统一为 6 位裸代码）
  const quoteMap = useMemo(() => {
    const map: Record<string, any> = {}
    const data = quoteQuery.data as any
    const quoteData = data?.data
    if (quoteData?.item) {
      for (const item of quoteData.item) {
        const code = (item.symbol || item.ticker || '').split('.')[0]
        if (code) {
          map[code] = item
        }
      }
    }
    return map
  }, [quoteQuery.data])

  // 过滤和排序
  const filteredItems = useMemo(() => {
    let list = items
    if (activeGroup !== 'all') {
      list = list.filter(i => i.group === activeGroup)
    }
    if (search) {
      const q = search.toLowerCase()
      list = list.filter(i =>
        i.symbol.includes(q) ||
        (i.name && i.name.toLowerCase().includes(q))
      )
    }
    list = [...list].sort((a, b) => {
      if (sortBy === 'code') {
        return a.symbol.localeCompare(b.symbol)
      }
      const pa = quoteMap[a.symbol]?.price_change_ratio_pct ?? 0
      const pb = quoteMap[b.symbol]?.price_change_ratio_pct ?? 0
      return pb - pa
    })
    return list
  }, [items, activeGroup, search, sortBy, quoteMap])

  // 添加自选股
  const handleAdd = () => {
    const code = addCode.trim().replace(/\D/g, '').slice(0, 6)
    if (code.length !== 6) return
    api.watchlistAddItem(code, addName.trim(), addGroup)
      .then(applyState)
      .catch(e => setError((e as Error).message))
    setAddCode('')
    setAddName('')
    setShowAdd(false)
  }

  // 批量添加（走 import 合并接口）
  const handleBatchAdd = (codes: string) => {
    const codeList = codes.split(/[,，\s]+/).map(c => c.trim().replace(/\D/g, '').slice(0, 6)).filter(c => c.length === 6)
    if (codeList.length === 0) return
    const newItems = codeList.map(c => ({ symbol: c, group: addGroup }))
    api.watchlistImport(newItems, [])
      .then(applyState)
      .catch(e => setError((e as Error).message))
    setAddCode('')
    setShowAdd(false)
  }

  // 删除自选股
  const handleRemove = (symbol: string) => {
    api.watchlistRemoveItem(symbol).then(applyState).catch(e => setError((e as Error).message))
  }

  // 勾选（checkbox 列；行点击是跳转个股页，需 stopPropagation）
  const toggleSelect = (symbol: string, e?: React.MouseEvent) => {
    e?.stopPropagation()
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(symbol)) next.delete(symbol)
      else next.add(symbol)
      return next
    })
  }

  // 全选/取消全选：作用于当前过滤后的可见列表
  const allVisibleChecked = filteredItems.length > 0 && filteredItems.every(i => selected.has(i.symbol))
  const toggleAll = () => {
    setSelected(prev => {
      const next = new Set(prev)
      if (allVisibleChecked) {
        for (const i of filteredItems) next.delete(i.symbol)
      } else {
        for (const i of filteredItems) next.add(i.symbol)
      }
      return next
    })
  }

  // 批量删除：确认后逐只删（复用现有单删接口，最后一只的返回态为准）
  const handleBatchRemove = async () => {
    if (selected.size === 0 || batchDeleting) return
    if (!window.confirm(`删除选中的 ${selected.size} 只自选股？此操作不可恢复`)) return
    setBatchDeleting(true)
    try {
      let lastState: any = null
      for (const symbol of selected) {
        lastState = await api.watchlistRemoveItem(symbol)
      }
      applyState(lastState)
      setSelected(new Set())
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBatchDeleting(false)
    }
  }

  // 行内换组
  const handleSetGroup = (symbol: string, group: string) => {
    api.watchlistSetGroup(symbol, group).then(applyState).catch(e => setError((e as Error).message))
  }

  // 新建自定义分组
  const handleAddGroup = () => {
    const name = newGroupName.trim()
    if (!name) return
    api.watchlistAddGroup(name)
      .then((state) => {
        applyState(state)
        const g = (state.groups as WatchGroup[]).find(x => x.name === name)
        if (g) {
          setAddGroup(g.id)
          setActiveGroup(g.id)
        }
      })
      .catch(e => setError((e as Error).message))
    setNewGroupName('')
    setShowNewGroup(false)
  }

  // 删除分组（组内股票移到默认分组）
  const handleDeleteGroup = (id: string) => {
    if (id === 'default') return
    const g = groups.find(x => x.id === id)
    if (!g) return
    if (!window.confirm(`删除分组「${g.name}」？组内股票将移到默认分组`)) return
    api.watchlistDeleteGroup(id).then((state) => {
      applyState(state)
      if (activeGroup === id) setActiveGroup('all')
      if (addGroup === id) setAddGroup('default')
    }).catch(e => setError((e as Error).message))
  }

  const formatPrice = (v: any) => v != null ? Number(v).toFixed(2) : '--'
  // price_change_ratio_pct 已是百分数（-7.06 = -7.06%），不要再 ×100
  const formatPct = (v: any) => v != null ? `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%` : '--'
  const formatAmount = (v: any) => {
    if (v == null) return '--'
    const n = Number(v)
    return Math.abs(n) >= 1e8 ? `${(n / 1e8).toFixed(2)}亿` : `${(n / 1e4).toFixed(0)}万`
  }
  const pctColor = (v: any) => {
    const n = Number(v ?? 0)
    return n > 0 ? 'text-rose-400' : n < 0 ? 'text-emerald-400' : 'text-slate-400'
  }

  return (
    <div className="space-y-4">
      {/* 标题 */}
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">自选股</h1>
          <div className="mt-1 text-xs text-slate-500">{items.length} 只 · 10秒自动刷新 · 云端同步</div>
        </div>
        <div className="flex items-center gap-2">
          {selected.size > 0 && (
            <button
              onClick={handleBatchRemove}
              disabled={batchDeleting}
              className="flex items-center gap-1 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 text-xs text-rose-300 hover:bg-rose-500/20 disabled:opacity-50"
            >
              <Trash2 className="h-3.5 w-3.5" />
              {batchDeleting ? '删除中…' : `删除选中（${selected.size}）`}
            </button>
          )}
          <button
            onClick={() => setShowAdd(!showAdd)}
            className="flex items-center gap-1 rounded-lg bg-brand/20 px-3 py-1.5 text-xs text-brand hover:bg-brand/30"
          >
            <Plus className="h-4 w-4" />
            添加自选
          </button>
        </div>
      </header>

      {error && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
          {error}
        </div>
      )}

      {/* 添加表单 */}
      {showAdd && (
        <div className="rounded-xl border border-surface-700 bg-surface-850 p-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
            <div>
              <label className="mb-1 block text-[11px] text-slate-500">股票代码（支持批量，逗号分隔）</label>
              <input
                type="text"
                value={addCode}
                onChange={e => setAddCode(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleAdd()}
                placeholder="600519 或 600519,000858"
                className="w-full rounded border border-surface-600 bg-surface-800 px-3 py-1.5 text-xs text-slate-200"
              />
            </div>
            <div>
              <label className="mb-1 block text-[11px] text-slate-500">名称（自动识别）</label>
              <div className="relative">
                <input
                  type="text"
                  value={addName}
                  onChange={e => setAddName(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleAdd()}
                  placeholder={nameLoading ? '查询中...' : '自动显示或手动输入'}
                  className="w-full rounded border border-surface-600 bg-surface-800 px-3 py-1.5 pr-7 text-xs text-slate-200 placeholder-slate-500"
                />
                {nameLoading && (
                  <div className="absolute right-2 top-1/2 -translate-y-1/2">
                    <div className="h-3 w-3 animate-spin rounded-full border-2 border-brand border-t-transparent"></div>
                  </div>
                )}
                {!nameLoading && addName && (
                  <div className="absolute right-2 top-1/2 -translate-y-1/2 text-emerald-400 text-xs">✓</div>
                )}
              </div>
            </div>
            <div>
              <label className="mb-1 block text-[11px] text-slate-500">分组</label>
              <select
                value={addGroup}
                onChange={e => setAddGroup(e.target.value)}
                className="w-full rounded border border-surface-600 bg-surface-800 px-3 py-1.5 text-xs text-slate-200"
              >
                {groups.map(g => (
                  <option key={g.id} value={g.id}>{g.name}</option>
                ))}
              </select>
            </div>
            <div className="flex items-end gap-2">
              <button
                onClick={addCode.includes(',') || addCode.includes('，') ? () => handleBatchAdd(addCode) : handleAdd}
                className="rounded-lg bg-brand px-4 py-1.5 text-xs text-white hover:bg-brand/80"
              >
                添加
              </button>
              <button
                onClick={() => setShowAdd(false)}
                className="rounded-lg border border-surface-600 px-4 py-1.5 text-xs text-slate-300 hover:bg-surface-700"
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 分组标签 + 搜索 + 排序 */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1">
          <button
            onClick={() => setActiveGroup('all')}
            className={`rounded px-2 py-1 text-[11px] ${
              activeGroup === 'all' ? 'bg-brand/20 text-brand' : 'text-slate-400 hover:bg-surface-700'
            }`}
          >
            全部 ({items.length})
          </button>
          {groups.map(g => {
            const count = items.filter(i => i.group === g.id).length
            if (count === 0 && g.id === 'default') return null
            return (
              <span key={g.id} className="group inline-flex items-center">
                <button
                  onClick={() => setActiveGroup(g.id)}
                  className={`rounded px-2 py-1 text-[11px] ${
                    activeGroup === g.id ? 'bg-brand/20 text-brand' : 'text-slate-400 hover:bg-surface-700'
                  }`}
                >
                  {g.name} ({count})
                </button>
                {g.id !== 'default' && (
                  <button
                    onClick={() => handleDeleteGroup(g.id)}
                    title={`删除分组「${g.name}」`}
                    className="-ml-1 hidden rounded px-0.5 text-[10px] text-slate-500 hover:text-rose-400 group-hover:inline"
                  >
                    ✕
                  </button>
                )}
              </span>
            )
          })}
          {showNewGroup ? (
            <span className="inline-flex items-center gap-1">
              <input
                type="text"
                value={newGroupName}
                onChange={e => setNewGroupName(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleAddGroup()}
                placeholder="分组名称"
                autoFocus
                className="w-24 rounded border border-brand/50 bg-surface-800 px-2 py-1 text-[11px] text-slate-200 focus:outline-none"
              />
              <button
                onClick={handleAddGroup}
                className="rounded bg-brand/20 px-2 py-1 text-[11px] text-brand hover:bg-brand/30"
              >
                确定
              </button>
              <button
                onClick={() => { setShowNewGroup(false); setNewGroupName('') }}
                className="text-[11px] text-slate-500 hover:text-slate-300"
              >
                ✕
              </button>
            </span>
          ) : (
            <button
              onClick={() => setShowNewGroup(true)}
              title="新建自定义分组"
              className="rounded border border-dashed border-surface-600 px-2 py-1 text-[11px] text-slate-500 hover:border-brand/50 hover:text-brand"
            >
              + 新建分组
            </button>
          )}
        </div>

        <div className="ml-auto flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="搜索..."
              className="w-44 rounded border border-surface-600 bg-surface-800 py-1.5 pl-7 pr-3 text-xs text-slate-200"
            />
          </div>
          <button
            onClick={() => setSortBy(sortBy === 'change' ? 'code' : 'change')}
            className="flex items-center gap-1 rounded border border-surface-600 px-2 py-1.5 text-[11px] text-slate-400 hover:bg-surface-700"
          >
            <ArrowUpDown className="h-3 w-3" />
            {sortBy === 'change' ? '按涨幅' : '按代码'}
          </button>
        </div>
      </div>

      {/* 自选股列表 */}
      {items.length === 0 ? (
        <div className="rounded-xl border border-dashed border-surface-600 bg-surface-850/50 py-16 text-center">
          <Star className="mx-auto mb-3 h-8 w-8 text-slate-600" />
          <p className="text-sm text-slate-500">
            还没有自选股，点击「添加自选」开始
          </p>
          <p className="mt-1 text-xs text-slate-600">
            支持批量添加：600519,000858,000001
          </p>
        </div>
      ) : (
        <div className="rounded-xl border border-surface-700 bg-surface-850 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-surface-800">
                <tr className="text-left text-slate-500 text-xs">
                  <th className="w-9 px-2 py-3 text-center" title="全选当前列表">
                    <input
                      type="checkbox"
                      checked={allVisibleChecked}
                      onChange={toggleAll}
                      className="accent-brand"
                      aria-label="全选"
                    />
                  </th>
                  <th className="px-4 py-3">代码</th>
                  <th className="px-4 py-3">名称</th>
                  <th className="px-4 py-3 text-right">现价</th>
                  <th className="px-4 py-3 text-right">涨跌幅</th>
                  <th className="px-4 py-3 text-right">涨跌额</th>
                  <th className="px-4 py-3 text-right">成交额</th>
                  <th className="px-4 py-3">分组</th>
                  <th className="px-4 py-3">操作</th>
                </tr>
              </thead>
              <tbody>
                {filteredItems.map(item => {
                  const q = quoteMap[item.symbol]
                  const price = q?.last_price
                  const changePct = q?.price_change_ratio_pct
                  const changeAmt = q?.price_change
                  const amount = q?.turnover

                  return (
                    <tr
                      key={item.symbol}
                      className="border-t border-surface-700 hover:bg-surface-800 cursor-pointer"
                      onClick={() => window.location.href = `/?code=${item.symbol}`}
                    >
                      <td
                        className="px-2 py-3 text-center"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <input
                          type="checkbox"
                          checked={selected.has(item.symbol)}
                          onChange={() => toggleSelect(item.symbol)}
                          className="accent-brand"
                          aria-label={`选择 ${item.name || item.symbol}`}
                        />
                      </td>
                      <td className="px-4 py-3 font-mono text-slate-300">{item.symbol}</td>
                      <td className="px-4 py-3 text-slate-200">{item.name || q?.name || '--'}</td>
                      <td className="px-4 py-3 text-right font-semibold text-slate-100">
                        {formatPrice(price)}
                      </td>
                      <td className={`px-4 py-3 text-right font-semibold ${pctColor(changePct)}`}>
                        {formatPct(changePct)}
                      </td>
                      <td className={`px-4 py-3 text-right ${pctColor(changeAmt)}`}>
                        {changeAmt != null ? `${Number(changeAmt) >= 0 ? '+' : ''}${Number(changeAmt).toFixed(2)}` : '--'}
                      </td>
                      <td className="px-4 py-3 text-right text-slate-400">
                        {formatAmount(amount)}
                      </td>
                      <td className="px-4 py-3">
                        <select
                          value={item.group}
                          onClick={(e) => e.stopPropagation()}
                          onChange={(e) => handleSetGroup(item.symbol, e.target.value)}
                          className="rounded border border-surface-600 bg-surface-700 px-1.5 py-0.5 text-[10px] text-slate-300 focus:border-brand focus:outline-none"
                        >
                          {groups.map(g => (
                            <option key={g.id} value={g.id}>{g.name}</option>
                          ))}
                        </select>
                      </td>
                      <td className="px-4 py-3">
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            handleRemove(item.symbol)
                          }}
                          className="text-slate-500 hover:text-rose-400"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {filteredItems.length > 0 && (
        <div className="text-xs text-slate-500">
          显示 {filteredItems.length} / {items.length} 只
          {quoteQuery.isFetching && <span className="ml-2 text-brand">刷新中...</span>}
        </div>
      )}
    </div>
  )
}
