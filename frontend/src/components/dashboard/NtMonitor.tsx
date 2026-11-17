import { useQuery } from '@tanstack/react-query'

import { api } from '../../lib/api'
import type { Envelope } from '../../lib/types'

/**
 * 国家队宽基 ETF 份额「当日增持」监控：
 * 周频披露按交易日均摊的估计值（非真实逐日值），非交易日不显示。
 * 数据到最新完整披露周，之后等下周五更新。
 */
type NtDay = { date: string; daily_change_yi: number; state: string }

export function NtMonitor() {
  const q = useQuery<Envelope>({
    queryKey: ['nt-monitor'],
    queryFn: () => api.ntMonitor(6),
    staleTime: 30 * 60_000,
  })
  const d = (q.data?.data as any) ?? null
  const ok = d?.status === 'ok'
  const latest = d?.latest as NtDay | undefined
  const series: NtDay[] = (d?.series as NtDay[]) ?? []
  const weekly = d?.weekly_latest as { date?: string; wchg_pct?: number } | undefined

  return (
    <section className="rounded-xl border border-surface-700 bg-surface-850 p-4">
      <header className="mb-2 flex items-center gap-2">
        <h2 className="text-sm font-semibold text-slate-100">国家队增持</h2>
        <span className="text-[9px] text-slate-600" title={d?.note ?? ''}>
          ⓘ {d?.source === 'daily' ? '真实日频 · 仅参考' : '周内均摊估计 · 仅参考'}
        </span>
        <span className="ml-auto text-[10px] text-slate-500">
          {weekly?.date ? `周档 ${weekly.date}` : ''}
        </span>
      </header>

      {!ok ? (
        <p className="text-[12px] text-slate-500">
          {q.isLoading ? '加载中…' : (d?.error ?? '份额数据不可用')}
        </p>
      ) : (
        <>
          <div className="mb-2 flex items-baseline gap-2">
            <span
              className={`text-lg font-bold ${
                latest?.state === '增持' ? 'text-red-400'
                  : latest?.state === '减持' ? 'text-emerald-400' : 'text-slate-400'
              }`}
            >
              {latest?.state === '增持' ? '在增持' : latest?.state === '减持' ? '在减持' : '持平'}
            </span>
            <span className="text-[12px] text-slate-400">
              {latest ? `${latest.daily_change_yi > 0 ? '+' : ''}${latest.daily_change_yi} 亿份/日` : ''}
            </span>
            <span className="ml-auto text-[10px] text-slate-600">{latest?.date ?? ''}</span>
          </div>
          <ul className="space-y-0.5">
            {series.map((r) => (
              <li key={r.date} className="flex items-center gap-2 text-[11px]">
                <span className="w-12 text-slate-500">{r.date.slice(5)}</span>
                <span
                  className={`flex-1 text-right ${
                    r.daily_change_yi > 0 ? 'text-red-400/90' : 'text-emerald-400/90'
                  }`}
                >
                  {r.daily_change_yi > 0 ? '+' : ''}{r.daily_change_yi}
                </span>
                <span className="w-8 text-slate-600">{r.state}</span>
              </li>
            ))}
          </ul>
          {weekly?.wchg_pct != null && (
            <p className="mt-1.5 text-[10px] text-slate-600">
              最近完整周净申购 {weekly.wchg_pct > 0 ? '+' : ''}{weekly.wchg_pct}%
            </p>
          )}
        </>
      )}
    </section>
  )
}
