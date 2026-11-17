import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { SearchItem } from '../lib/types'

/**
 * 股票搜索框：防抖调用 /api/symbol-search，聚焦时列出候选。
 * 交互借鉴 tickflow 面板的 StockFinancialSearch。
 */
export default function StockSearch({
  onSelect,
  placeholder,
}: {
  onSelect: (item: SearchItem) => void
  placeholder?: string
}) {
  const [kw, setKw] = useState('')
  const [open, setOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)

  const { data, isFetching } = useQuery({
    queryKey: ['symbolSearch', kw],
    queryFn: () => api.symbolSearch(kw),
    enabled: kw.trim().length >= 1,
    staleTime: 30_000,
  })

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [])

  const items = data?.items ?? []

  return (
    <div ref={boxRef} className="relative w-full max-w-md">
      <div className="flex items-center gap-2 rounded-lg border border-surface-600 bg-surface-800 px-3 py-2 focus-within:border-brand/60">
        <span className="text-slate-500">🔍</span>
        <input
          value={kw}
          onChange={(e) => {
            setKw(e.target.value)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && items[0]) {
              onSelect(items[0])
              setOpen(false)
            }
          }}
          placeholder={placeholder ?? '输入股票代码或名称，如 002837 / 英维克'}
          className="w-full bg-transparent text-sm text-slate-200 outline-none placeholder:text-slate-500"
        />
        {isFetching && <span className="text-xs text-slate-500">查询中…</span>}
      </div>

      {open && kw.trim().length >= 1 && (
        <div className="absolute z-20 mt-1 w-full overflow-hidden rounded-lg border border-surface-600 bg-surface-850 shadow-xl">
          {items.length === 0 && !isFetching && (
            <div className="px-3 py-2 text-xs text-slate-500">没有匹配的 A 股标的</div>
          )}
          {items.map((it) => (
            <button
              key={it.thscode}
              onClick={() => {
                onSelect(it)
                setOpen(false)
                setKw(it.name)
              }}
              className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-surface-700"
            >
              <span className="text-slate-200">{it.name}</span>
              <span className="text-xs text-slate-500">{it.thscode}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}