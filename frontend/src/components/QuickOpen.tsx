import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { Modal } from './Modal'

const PAGES = [
  { to: '/', label: '个股分析' },
  { to: '/dashboard', label: '实时看板' },
  { to: '/market-overview', label: '市场总览' },
  { to: '/sector', label: '板块分析' },
  { to: '/review', label: '大盘复盘' },
  { to: '/watchlist', label: '自选股' },
  { to: '/screen', label: '策略选股' },
  { to: '/pool', label: '产业链选股' },
  { to: '/backtest', label: '回测' },
  { to: '/paper', label: '模拟盘' },
  { to: '/news', label: '产业资讯' },
  { to: '/settings', label: '设置' },
]

/** Ctrl+K 快捷入口（A2-8）：股票搜索 + 页面跳转。 */
export default function QuickOpen() {
  const [open, setOpen] = useState(false)
  const [kw, setKw] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const nav = useNavigate()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen((v) => !v)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 0)
  }, [open])

  const { data } = useQuery({
    queryKey: ['quickopen-stock', kw],
    queryFn: () => api.symbolSearch(kw),
    enabled: kw.trim().length >= 1,
    staleTime: 30_000,
  })
  const stocks = data?.items ?? []
  const pages = PAGES.filter((p) =>
    !kw.trim() || p.label.includes(kw.trim()) || p.to.includes(kw.trim()))

  return open ? (
    <Modal onClose={() => setOpen(false)} ariaLabel="快捷打开"
      panelClassName="w-[92vw] max-w-xl rounded-xl border border-surface-700 bg-surface-850 shadow-2xl">
      <div className="p-3">
        <input
          ref={inputRef}
          value={kw}
          onChange={(e) => setKw(e.target.value)}
          placeholder="输入股票代码/名称，或页面名…"
          className="w-full rounded-lg border border-surface-600 bg-surface-800 px-3 py-2 text-sm text-slate-200 outline-none focus:border-brand/60"
        />
        <div className="mt-2 max-h-[55vh] overflow-y-auto">
          {pages.length > 0 && (
            <div className="mb-1 px-2 py-1 text-[10px] uppercase text-slate-500">页面</div>
          )}
          {pages.map((p) => (
            <button key={p.to} onClick={() => { nav(p.to); setOpen(false); setKw('') }}
              className="block w-full rounded px-3 py-1.5 text-left text-sm text-slate-300 hover:bg-surface-700">
              {p.label} <span className="ml-1 text-[10px] text-slate-500">{p.to}</span>
            </button>
          ))}
          {stocks.length > 0 && (
            <div className="mb-1 mt-2 px-2 py-1 text-[10px] uppercase text-slate-500">股票</div>
          )}
          {stocks.map((s: any) => (
            <button key={s.thscode}
              onClick={() => { nav(`/?code=${s.thscode.split('.')[0]}`); setOpen(false); setKw('') }}
              className="flex w-full items-center justify-between rounded px-3 py-1.5 text-left text-sm hover:bg-surface-700">
              <span className="text-slate-200">{s.name}</span>
              <span className="text-[10px] text-slate-500">{s.thscode}</span>
            </button>
          ))}
          {kw.trim() && pages.length === 0 && stocks.length === 0 && (
            <div className="px-3 py-2 text-xs text-slate-500">没有匹配结果</div>
          )}
        </div>
      </div>
    </Modal>
  ) : null
}
