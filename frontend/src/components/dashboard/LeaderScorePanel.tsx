import { useQuery } from '@tanstack/react-query'
import { getJson } from '../../lib/api'
import { fmtStockPct, pctClass } from './format'

const FACTORS = [
  ['momentum', '动能', 35], ['turnover', '换手', 22], ['amount', '成交额', 18],
  ['cap', '市值', 15], ['volume', '量比', 7], ['boards', '连板', 3],
] as const

function rawValue(key: string, v: number | null) {
  if (v === null || v === undefined) return '—'
  if (key === 'momentum') return fmtStockPct(v)
  if (key === 'turnover') return `${v.toFixed(2)}%`
  if (key === 'amount' || key === 'cap') return `${(v / 1e8).toFixed(1)}亿`
  if (key === 'volume') return `${v.toFixed(2)}倍`
  return `${v}板`
}

/** 龙头六因子评分与拆解（B3）：缺因子显示覆盖率，不把缺数据包装成完整分。 */
export function LeaderScorePanel({ kind, name }: { kind: string; name: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['sector-leader-scores', kind, name],
    queryFn: () => getJson<any>(`/api/sector-leader-scores?kind=${encodeURIComponent(kind)}&name=${encodeURIComponent(name)}`),
    staleTime: 60_000,
    retry: 1,
  })
  if (isLoading) return <div className="py-8 text-center text-xs text-slate-500">正在计算六因子评分…</div>
  if (!data?.available) return <div className="py-5 text-center text-xs text-slate-500">暂无评分数据</div>

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <div className="rounded-lg bg-rose-500/10 p-2 text-center">
          <div className="text-[10px] text-slate-500">板块热度</div>
          <div className="text-lg font-semibold text-rose-300">{data.heat_score}</div>
        </div>
        <div className="rounded-lg bg-emerald-500/10 p-2 text-center">
          <div className="text-[10px] text-slate-500">板块风险</div>
          <div className="text-lg font-semibold text-emerald-300">{data.risk_score}</div>
        </div>
        <div className="rounded-lg bg-surface-800 p-2 text-center">
          <div className="text-[10px] text-slate-500">平均涨幅</div>
          <div className={`text-sm font-semibold ${pctClass(data.avg_pct)}`}>{fmtStockPct(data.avg_pct)}</div>
        </div>
        <div className="rounded-lg bg-surface-800 p-2 text-center">
          <div className="text-[10px] text-slate-500">上涨占比</div>
          <div className="text-sm font-semibold text-slate-200">{(data.up_rate * 100).toFixed(1)}%</div>
        </div>
      </div>
      <div className="text-[10px] text-slate-500">
        {data.as_of} · {data.total} 只 · 前 8 名；点击个股可跳分析页。覆盖率不足时，缺失因子贡献为 0，分数会偏低。
      </div>
      {data.rows.slice(0, 8).map((r: any, idx: number) => (
        <div key={r.symbol} className="rounded-lg border border-surface-700 bg-surface-800/50 p-2.5">
          <div className="mb-2 flex items-center gap-2">
            <span className="w-5 text-center text-xs text-slate-500">{idx + 1}</span>
            <a href={`/?code=${r.symbol.split('.')[0]}`} className="text-xs font-medium text-slate-200 hover:text-brand">
              {r.name || r.symbol}
            </a>
            <span className="text-[10px] text-slate-600">{r.symbol}</span>
            <span className="ml-auto text-base font-bold text-amber-300">{r.score.toFixed(1)}</span>
            <span className={`rounded px-1.5 py-0.5 text-[9px] ${r.coverage >= 1 ? 'bg-surface-700 text-slate-500' : 'bg-amber-500/10 text-amber-300'}`}
              title={r.missing.length ? `缺失：${r.missing.join('、')}` : '六因子完整'}>
              覆盖 {(r.coverage * 100).toFixed(0)}%
            </span>
          </div>
          <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 md:grid-cols-3">
            {FACTORS.map(([key, label, weight]) => {
              const part = r.parts[key]
              return (
                <div key={key} title={`${label}权重 ${weight}% · 原始值 ${rawValue(key, r.raw[key])}`}>
                  <div className="mb-0.5 flex justify-between text-[9px] text-slate-500">
                    <span>{label} {weight}%</span>
                    <span>{rawValue(key, r.raw[key])}</span>
                  </div>
                  <div className="h-1 rounded bg-surface-700">
                    {part !== null && <div className="h-1 rounded bg-brand/70" style={{ width: `${part * 100}%` }} />}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      ))}
      <p className="text-[10px] leading-relaxed text-slate-600">{data.note}</p>
    </div>
  )
}
