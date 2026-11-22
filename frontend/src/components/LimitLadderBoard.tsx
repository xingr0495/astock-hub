import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { getJson } from '../lib/api'

type SectorKind = 'concept' | 'industry'
type SectorFilter = { kind: SectorKind; name: string } | null

/** 连板梯队逐层概念/行业分布（B2-6）：标签筛选股票，箭头查看完整成分。 */
export function LimitLadderBoard({ onSelectSector }: {
  onSelectSector: (s: { kind: SectorKind; name: string }) => void
}) {
  const [kind, setKind] = useState<SectorKind>('concept')
  const [filter, setFilter] = useState<SectorFilter>(null)
  const navigate = useNavigate()
  const { data, isLoading } = useQuery({
    queryKey: ['limit-ladder-distribution'],
    queryFn: () => getJson<any>('/api/market/limit-ladder'),
    staleTime: 300_000,
  })

  if (isLoading) return <div className="text-xs text-slate-500">连板梯队加载中…</div>
  if (!data?.available) {
    return <div className="rounded-xl border border-surface-700 bg-surface-850 p-6 text-center text-xs text-slate-500">
      暂无涨停池存档，无法生成分布。
    </div>
  }
  const listKey = kind === 'concept' ? 'concepts' : 'industries'

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-surface-700 bg-surface-850 p-3">
        <div className="text-xs text-slate-400">
          <span className="mr-2 font-medium text-slate-200">连板梯队 · {data.as_of}</span>
          共 {data.total} 只涨停；点击标签筛选，点 ↗ 查看完整板块成分
        </div>
        <div className="flex rounded-md bg-surface-800 p-0.5 text-[11px]">
          {(['concept', 'industry'] as SectorKind[]).map((k) => (
            <button key={k} onClick={() => setKind(k)}
              className={`rounded px-2.5 py-1 ${kind === k ? 'bg-brand/20 text-brand' : 'text-slate-400'}`}>
              {k === 'concept' ? '概念' : '行业'}
            </button>
          ))}
        </div>
      </div>

      {filter && (
        <div className="flex items-center gap-2 rounded-lg border border-brand/30 bg-brand/5 px-3 py-2 text-xs">
          <span className="text-slate-300">当前筛选：{filter.name}</span>
          <button onClick={() => setFilter(null)} className="ml-auto text-brand hover:underline">清除筛选</button>
        </div>
      )}

      {data.layers.map((layer: any) => {
        const stocks = (layer.stocks ?? []).filter((s: any) =>
          !filter || (s[filter.kind === 'concept' ? 'concepts' : 'industries'] ?? []).includes(filter.name))
        const chips = (layer[listKey] ?? []).slice(0, 14)
        return (
          <div key={layer.limit_days} className="rounded-xl border border-surface-700 bg-surface-850 p-3">
            <div className="mb-2 flex items-center gap-2">
              <span className={`rounded px-2 py-0.5 text-xs font-semibold ${layer.limit_days >= 4 ? 'bg-rose-500/15 text-rose-300' : 'bg-amber-500/10 text-amber-300'}`}>
                {layer.limit_days === 1 ? '首板' : `${layer.limit_days} 连板`}
              </span>
              <span className="text-[11px] text-slate-500">
                {filter ? `筛选后 ${stocks.length} / ` : ''}{layer.total} 只
              </span>
            </div>
            <div className="mb-2 flex flex-wrap gap-1.5">
              {chips.map((c: any) => {
                const active = filter?.kind === kind && filter.name === c.name
                return (
                  <span key={c.name} className={`inline-flex items-center rounded-md border text-[11px] ${active ? 'border-brand/60 bg-brand/15 text-brand' : 'border-surface-600 bg-surface-800 text-slate-300'}`}>
                    <button onClick={() => setFilter(active ? null : { kind, name: c.name })}
                      className="px-2 py-1" title="筛选本层股票">
                      {c.name} <b className="ml-0.5 text-rose-300">{c.count}</b>
                    </button>
                    <button onClick={() => onSelectSector({ kind, name: c.name })}
                      className="border-l border-surface-600 px-1.5 py-1 text-slate-500 hover:text-brand" title="查看完整板块成分">
                      ↗
                    </button>
                  </span>
                )
              })}
              {(layer[listKey]?.length ?? 0) > chips.length && (
                <span className="px-1 py-1 text-[10px] text-slate-600">另 {layer[listKey].length - chips.length} 个</span>
              )}
            </div>
            <div className="flex flex-wrap gap-1">
              {stocks.map((s: any) => (
                <button key={s.symbol} onClick={() => navigate(`/?code=${s.symbol.split('.')[0]}`)}
                  className="rounded bg-surface-800/70 px-2 py-1 text-[11px] text-slate-300 hover:bg-surface-700 hover:text-slate-100">
                  {s.name}
                </button>
              ))}
              {filter && stocks.length === 0 && <span className="text-[11px] text-slate-600">本层无匹配股票</span>}
            </div>
          </div>
        )
      })}
      <p className="text-[10px] text-slate-600">{data.note}；每只股票可属于多个概念，各标签计数不相加为总数。</p>
    </section>
  )
}
