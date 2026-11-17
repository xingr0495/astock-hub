import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'

/**
 * 今日主线：基于涨停梯队的概念/行业排名（涨停家数、最高连板、梯队完整度加权）。
 * 本地计算，不依赖 tickflow。
 */
export default function MainlineSection() {
  const [kind, setKind] = useState<'concept' | 'industry'>('concept')
  const query = useQuery({
    queryKey: ['mainline', 5],
    queryFn: () => api.mainline(5),
    staleTime: 5 * 60_000,
  })

  if (query.isLoading) {
    return <div className="py-10 text-center text-xs text-slate-500">正在计算今日主线（全市场涨停梯队）…</div>
  }
  const snap = query.data?.data as any
  const rows: any[] = snap?.kinds?.[kind] ?? []
  const latest = rows.length ? String(rows[rows.length - 1].date) : null
  const today = latest ? rows.filter((r) => String(r.date) === latest) : []

  return (
    <section className="rounded-xl border border-surface-700 bg-surface-850 p-4">
      <header className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold text-slate-100">今日主线</h2>
        <div className="ml-auto flex gap-1">
          {(['concept', 'industry'] as const).map((k) => (
            <button
              key={k}
              onClick={() => setKind(k)}
              className={`rounded border px-2 py-0.5 text-[10px] ${
                kind === k
                  ? 'border-brand/50 bg-brand/15 text-brand'
                  : 'border-surface-600 bg-surface-800 text-slate-400 hover:text-slate-200'
              }`}
            >
              {k === 'concept' ? '概念' : '行业'}
            </button>
          ))}
        </div>
      </header>

      {today.length === 0 ? (
        <p className="py-6 text-center text-xs text-slate-500">当日没有满足条件的主线（涨停家数过少）</p>
      ) : (
        <div className="space-y-1.5">
          {today.map((r) => (
            <div
              key={`${r.rank}-${r.member}`}
              className="flex items-center gap-3 rounded-md bg-surface-700/40 px-3 py-1.5 text-[12px]"
            >
              <span className="w-5 text-center text-[11px] font-semibold text-slate-400">{r.rank}</span>
              <span className="w-32 truncate font-medium text-slate-100">{r.member}</span>
              <span className="text-[11px] text-slate-400">
                涨停 <b className="text-rose-300">{r.limit_up_count}</b>
              </span>
              <span className="text-[11px] text-slate-400">
                连板 <b className="text-amber-300">{r.max_boards}</b>
              </span>
              {r.leader_symbol && (
                <Link
                  to={`/?code=${r.leader_symbol.split('.')[0]}`}
                  className="ml-auto text-[11px] text-brand hover:underline"
                  title="查看龙头股"
                >
                  龙头 {r.leader_symbol.split('.')[0]}
                </Link>
              )}
            </div>
          ))}
        </div>
      )}

      <p className="mt-3 text-[10px] leading-relaxed text-slate-500">
        {snap?.note ?? ''}。主线按"涨停家数/最高连板/梯队完整度"截面加权，
        仅统计当日涨停 ≥3 家的{kind === 'concept' ? '概念' : '行业'}。
      </p>
    </section>
  )
}
