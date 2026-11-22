import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { getJson } from '../lib/api'

const PHASE: Record<string, { label: string; className: string }> = {
  ice: { label: '❄️ 冰点', className: 'bg-sky-500/15 text-sky-300' },
  ignite: { label: '🔥 启动', className: 'bg-orange-500/15 text-orange-300' },
  rally: { label: '🚀 主升', className: 'bg-rose-500/15 text-rose-300' },
  climax: { label: '⚠️ 高潮', className: 'bg-amber-500/15 text-amber-300' },
  ebb: { label: '🌊 退潮', className: 'bg-emerald-500/15 text-emerald-300' },
  repair: { label: '🔧 修复', className: 'bg-slate-500/15 text-slate-300' },
  unknown: { label: '未知', className: 'bg-slate-500/15 text-slate-400' },
}

/** 阶段 × 主线历史矩阵（B2-7）：连续阶段段内 top5 主线。 */
export function PhaseMainlineMatrix({ onSelectSector }: {
  onSelectSector: (s: { kind: 'concept' | 'industry'; name: string }) => void
}) {
  const [kind, setKind] = useState<'concept' | 'industry'>('concept')
  const navigate = useNavigate()
  const { data, isLoading } = useQuery({
    queryKey: ['phase-mainline-matrix', kind],
    queryFn: () => getJson<any>(`/api/phase-mainline-matrix?days=120&kind=${kind}`),
    staleTime: 300_000,
  })
  if (isLoading) return <div className="text-xs text-slate-500">阶段主线矩阵加载中…</div>
  if (!data?.available) return <div className="text-xs text-slate-500">暂无阶段与主线历史数据。</div>

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-surface-700 bg-surface-850 p-3">
        <div className="text-xs text-slate-400">
          <span className="mr-2 font-medium text-slate-200">阶段 × 主线历史矩阵</span>
          近 120 日 · 截至 {data.as_of} · 每段取上榜天数前 5
        </div>
        <div className="flex rounded-md bg-surface-800 p-0.5 text-[11px]">
          {(['concept', 'industry'] as const).map((k) => (
            <button key={k} onClick={() => setKind(k)}
              className={`rounded px-2.5 py-1 ${kind === k ? 'bg-brand/20 text-brand' : 'text-slate-400'}`}>
              {k === 'concept' ? '概念' : '行业'}
            </button>
          ))}
        </div>
      </div>

      {data.segments.map((seg: any) => {
        const phase = PHASE[seg.phase] ?? PHASE.unknown
        return (
          <div key={`${seg.start}-${seg.phase}`} className="rounded-xl border border-surface-700 bg-surface-850 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <span className={`rounded px-2 py-0.5 text-xs ${phase.className}`}>{phase.label}</span>
              <span className="text-xs text-slate-300">{seg.start} ~ {seg.end}</span>
              <span className="text-[11px] text-slate-500">{seg.days} 个交易日</span>
              <span className="ml-auto text-[10px] text-slate-600">主线覆盖 {seg.mainline_days}/{seg.days} 日</span>
            </div>
            {seg.items.length === 0 ? (
              <div className="py-2 text-[11px] text-slate-600">该阶段段内无主线排名存档</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] border-collapse text-xs">
                  <thead><tr className="border-b border-surface-700 text-left text-[10px] text-slate-500">
                    <th className="py-1.5 font-normal">主线板块</th>
                    <th className="py-1.5 text-right font-normal">上榜天数</th>
                    <th className="py-1.5 text-right font-normal">最高连板</th>
                    <th className="py-1.5 font-normal">峰值日龙头</th>
                    <th className="py-1.5 text-right font-normal">峰值日期</th>
                  </tr></thead>
                  <tbody>
                    {seg.items.map((item: any) => (
                      <tr key={item.name} className="border-b border-surface-700/50 last:border-0">
                        <td className="py-2">
                          <button onClick={() => onSelectSector({ kind, name: item.name })}
                            className="text-slate-200 hover:text-brand">{item.name}</button>
                        </td>
                        <td className="py-2 text-right">
                          <span className="rounded bg-brand/10 px-1.5 py-0.5 text-brand">{item.days}/{seg.days}</span>
                        </td>
                        <td className="py-2 text-right font-semibold text-rose-300">{item.max_boards}</td>
                        <td className="py-2 font-mono text-[11px] text-slate-400">
                          {item.leader_symbol ? (
                            <button onClick={() => navigate(`/?code=${item.leader_symbol.split('.')[0]}`)}
                              className="hover:text-brand">{item.leader_symbol}</button>
                          ) : '—'}
                        </td>
                        <td className="py-2 text-right text-[11px] text-slate-500">{item.peak_date}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )
      })}
      <p className="text-[10px] leading-relaxed text-slate-600">{data.membership_note}</p>
    </section>
  )
}
