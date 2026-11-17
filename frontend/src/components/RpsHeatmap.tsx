import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'

/** 涨幅轮动热力矩阵：行=板块(最新日涨幅TOP25)、列=近N日、格=当日平均涨幅。
 *  红涨绿降（A股惯例），透明度随 |幅度| 分档；数据=/api/rps 本地计算。 */
const TOP_N = 25
const cellColor = (pct: number | null): string => {
  if (pct === null || Number.isNaN(pct)) return 'transparent'
  const a = Math.min(0.15 + Math.abs(pct) / 8, 0.85)
  return pct >= 0 ? `rgba(239,68,68,${a})` : `rgba(16,185,129,${a})`
}

export function RpsHeatmap({ onSelectSector }: {
  onSelectSector: (s: { kind: string; name: string }) => void
}) {
  const [kind, setKind] = useState<'concept' | 'industry'>('concept')
  const [days, setDays] = useState<7 | 12>(7)
  const { data, isLoading } = useQuery({
    queryKey: ['rps', days],
    queryFn: () => api.rps(days, false),
    staleTime: 60_000,
    refetchInterval: 30 * 60_000, // 本地日频数据，非盘中不需要快节奏
  })
  const snap = (data?.data as any) ?? {}
  const k = snap.kinds?.[kind] as { dates?: string[]; columns?: Record<string, [string, number][]> } | undefined
  const dates = [...(k?.dates ?? [])].slice(0, days).reverse() // 矩阵列按时间正序
  const cols = k?.columns ?? {}
  const latest = dates[dates.length - 1]
  const top = (cols[latest] ?? []).slice(0, TOP_N)
  const avgOf = (name: string) => {
    const vals = dates.map((d) => (cols[d] ?? []).find(([m]) => m === name)?.[1]).filter((v): v is number => typeof v === 'number')
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null
  }

  return (
    <div className="rounded-xl border border-surface-700 bg-surface-850 p-4">
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-sm font-medium text-slate-200">板块涨幅轮动矩阵</h2>
        <div className="ml-auto flex gap-1">
          {(['concept', 'industry'] as const).map((x) => (
            <button key={x} onClick={() => setKind(x)}
              className={`rounded border px-2 py-0.5 text-[10px] ${kind === x ? 'border-brand/50 bg-brand/15 text-brand' : 'border-surface-600 text-slate-400 hover:text-slate-200'}`}>
              {x === 'concept' ? '概念' : '行业'}
            </button>
          ))}
          {([7, 12] as const).map((d) => (
            <button key={d} onClick={() => setDays(d)}
              className={`rounded border px-2 py-0.5 text-[10px] ${days === d ? 'border-brand/50 bg-brand/15 text-brand' : 'border-surface-600 text-slate-400 hover:text-slate-200'}`}>
              {d}日
            </button>
          ))}
        </div>
      </div>
      {isLoading ? (
        <div className="py-10 text-center text-xs text-slate-400">本地全市场计算中，首次较慢…</div>
      ) : top.length === 0 ? (
        <div className="py-10 text-center text-xs text-slate-500">{(snap.hint ?? (snap.data as any)?.hint) ?? '暂无数据'}</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="border-collapse text-[10px]">
            <thead>
              <tr className="text-slate-500">
                <th className="sticky left-0 bg-surface-850 px-2 text-left font-normal">板块</th>
                {dates.map((d) => <th key={d} className="px-1 text-center font-normal tabular-nums">{d.slice(5)}</th>)}
                <th className="px-2 text-center text-slate-600">均值</th>
              </tr>
            </thead>
            <tbody>
              {top.map(([name]) => {
                const avg = avgOf(name)
                return (
                  <tr key={name} className="border-t border-surface-700/40">
                    <td onClick={() => onSelectSector({ kind, name })}
                      className="sticky left-0 max-w-32 cursor-pointer truncate bg-surface-850 px-2 py-1 text-slate-200 hover:text-brand">
                      {name}
                    </td>
                    {dates.map((d) => {
                      const v = (cols[d] ?? []).find(([m]) => m === name)?.[1]
                      const pct = typeof v === 'number' ? v * 100 : null
                      return (
                        <td key={d} title={pct === null ? undefined : `${d} · ${name} · ${pct.toFixed(2)}%`}
                          className="px-1 py-1 text-center tabular-nums text-slate-200"
                          style={{ backgroundColor: cellColor(pct) }}>
                          {pct === null ? '·' : pct.toFixed(1)}
                        </td>
                      )
                    })}
                    <td className="px-2 py-1 text-center tabular-nums text-slate-400">
                      {avg === null ? '·' : (avg * 100).toFixed(1)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          <p className="mt-2 text-[10px] text-slate-600">
            格值=板块当日平均涨幅（%），红涨绿降、色深随幅度；行按最新一日涨幅取前 {TOP_N}。
            与"近7日轮动记录"（按涨停梯队排名）互补：看梯队用那个，看涨幅持续性看这个。
          </p>
        </div>
      )}
    </div>
  )
}
