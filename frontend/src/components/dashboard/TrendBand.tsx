import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import Sparkline from '../Sparkline'

/**
 * 趋势带（A·复盘模式"位置感"）：情绪分/涨停家数/上涨占比的 7-10 日迷你走势，
 * 今天的点是红色高亮——一眼看出"在周期什么位置、往哪走"。
 * 数据源 /api/regime（本地全市场计算，非实时帧）。
 */
const TILES = [
  { label: '情绪分', key: 'score', color: '#38bdf8', fmt: (v: number) => String(Math.round(v)) },
  { label: '涨停家数', key: 'limit_up', color: '#fb7185', fmt: (v: number) => String(Math.round(v)) },
  { label: '上涨占比', key: 'up_ratio', color: '#fbbf24', fmt: (v: number) => `${(v * 100).toFixed(0)}%` },
] as const

export function TrendBand({ asOf }: { asOf?: string }) {
  const q = useQuery({
    queryKey: ['regime-trend'],
    queryFn: () => api.regime(10, false),
    staleTime: 5 * 60_000,
    refetchInterval: asOf ? false : 5 * 60_000,
  })
  const history = ((q.data?.data as any)?.history ?? []) as {
    date: string
    score: number
    limit_up: number
    up_ratio: number
    state?: string
  }[]

  if (q.isLoading) {
    return <div className="rounded-xl border border-surface-700 bg-surface-850 px-3 py-2 text-[11px] text-slate-500">趋势带加载中…</div>
  }
  // 回看模式：截断到所选日为止（与"当天看"同窗口）
  const upto = asOf ? history.filter((r) => r.date <= asOf) : history
  const recent = upto.slice(-10)
  if (recent.length < 2) return null

  return (
    <div className="rounded-xl border border-surface-700 bg-surface-850 px-3 py-2">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {TILES.map((t) => {
          const values = recent.map((r) => Number((r as any)[t.key] ?? 0))
          const last = values[values.length - 1]
          const prev = values[values.length - 2]
          const up = last >= prev
          return (
            <div key={t.key} className="flex items-center gap-2">
              <div className="min-w-0">
                <div className="text-[10px] text-slate-500">{t.label} · 近{values.length}日</div>
                <div className="flex items-baseline gap-1">
                  <span className="text-sm font-semibold" style={{ color: t.color }}>
                    {t.fmt(last)}
                  </span>
                  <span className={`text-[10px] ${up ? 'text-rose-400' : 'text-emerald-400'}`}>
                    {up ? '▲' : '▼'}
                  </span>
                </div>
              </div>
              <Sparkline values={values} width={140} height={30} color={t.color} />
            </div>
          )
        })}
      </div>
      <div className="mt-1 text-right text-[9px] text-slate-600">
        区间 {recent[0].date.slice(5)} ~ {recent[recent.length - 1].date.slice(5)} 
      </div>
    </div>
  )
}
