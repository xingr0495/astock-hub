import { useQuery } from '@tanstack/react-query'
import { getJson } from '../../lib/api'
import { fmtStockPct, pctClass } from './format'
import { useChartTheme } from '../../lib/theme'

type Point = { time: string; sector: number | null; market: number | null }

/** 板块等权分时 vs 全市场（B2-5）：点击后加载，盘后分钟库回放。 */
export function SectorIntradayChart({ kind, name }: { kind: string; name: string }) {
  const ct = useChartTheme()
  const { data, isLoading } = useQuery({
    queryKey: ['sector-intraday', kind, name],
    queryFn: () => getJson<any>(`/api/sector-intraday?kind=${encodeURIComponent(kind)}&name=${encodeURIComponent(name)}`),
    staleTime: 60_000,
    retry: 1,
  })
  if (isLoading) return <div className="py-8 text-center text-xs text-slate-500">正在计算板块分时…</div>
  if (!data?.available) {
    return <div className="rounded-lg bg-surface-800/60 px-3 py-5 text-center text-xs text-slate-500">
      该板块暂无分钟数据，无法绘制分时对比。
    </div>
  }

  const points: Point[] = data.points ?? []
  const values = points.flatMap((p) => [p.sector, p.market])
    .filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
  if (values.length === 0) return null
  let lo = Math.min(0, ...values)
  let hi = Math.max(0, ...values)
  const pad = Math.max((hi - lo) * 0.12, 0.002)
  lo -= pad
  hi += pad
  const W = 720, H = 210, left = 48, right = 12, top = 12, bottom = 24
  const x = (i: number) => left + (i / Math.max(points.length - 1, 1)) * (W - left - right)
  const y = (v: number) => top + ((hi - v) / (hi - lo)) * (H - top - bottom)
  const line = (key: 'sector' | 'market') => {
    let started = false
    return points.map((p, i) => {
      const v = p[key]
      if (v === null) return null
      const cmd = started ? 'L' : 'M'
      started = true
      return `${cmd}${x(i).toFixed(1)},${y(v).toFixed(1)}`
    }).filter(Boolean).join(' ')
  }
  const last = [...points].reverse().find((p) => p.sector !== null)
  const lastMarket = [...points].reverse().find((p) => p.market !== null)
  const marketAvailable = points.some((p) => p.market !== null)

  return (
    <div className="rounded-lg border border-surface-700 bg-surface-800/40 p-3">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-[11px]">
        <span className="text-slate-400">
          板块等权分时 vs 全市场 · {data.as_of}
        </span>
        <span className="flex gap-3">
          <span className="text-sky-300">板块 <b className={pctClass(last?.sector)}>{fmtStockPct(last?.sector)}</b></span>
          <span className="text-slate-400">全市场 {marketAvailable ? <b className={pctClass(lastMarket?.market)}>{fmtStockPct(lastMarket?.market)}</b> : <span className="text-amber-400">基准缺失</span>}</span>
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="板块与全市场等权分时对比">
        {[hi - pad, 0, lo + pad].map((v, i) => (
          <g key={i}>
            <line x1={left} x2={W - right} y1={y(v)} y2={y(v)}
              stroke={v === 0 ? (ct.name === 'light' ? ct.border : '#64748b') : (ct.name === 'light' ? ct.grid : '#334155')} strokeDasharray={v === 0 ? '4 3' : undefined} />
            <text x={left - 5} y={y(v) + 3} textAnchor="end" fontSize="10" fill={ct.text}>
              {(v * 100).toFixed(2)}%
            </text>
          </g>
        ))}
        <path d={line('market')} fill="none" stroke={ct.name === 'light' ? ct.text : '#94a3b8'} strokeWidth="1.5" />
        <path d={line('sector')} fill="none" stroke="#38bdf8" strokeWidth="2" />
        {[0, Math.floor((points.length - 1) / 2), points.length - 1].map((i) => (
          <text key={i} x={x(i)} y={H - 5} textAnchor={i === 0 ? 'start' : i === points.length - 1 ? 'end' : 'middle'}
            fontSize="10" fill="#64748b">{points[i]?.time}</text>
        ))}
      </svg>
      <div className="text-[10px] text-slate-500">
        覆盖 {data.covered}/{data.member_total} 只 · 全市场 {data.market_count} 只 · 等权平均 · 盘后分钟库，非实时
      </div>
    </div>
  )
}
