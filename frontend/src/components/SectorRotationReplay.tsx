import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import ReactECharts from './ThemedECharts'
import { getJson } from '../lib/api'
import { fmtStockPct, pctClass } from './dashboard/format'
import { useChartTheme } from '../lib/theme'

const COLORS = ['#38bdf8', '#fb7185', '#fbbf24', '#a78bfa', '#34d399', '#f97316', '#22d3ee', '#e879f9', '#94a3b8', '#f43f5e']

/** 盘中板块切换·分钟回放：对齐 TSP 的分钟走势/热力 + 1小时切换强度。 */
export default function SectorRotationReplay() {
  const ct = useChartTheme()
  const [kind, setKind] = useState<'concept' | 'industry'>('concept')
  const [bucket, setBucket] = useState(5)
  const [mode, setMode] = useState<'trend' | 'heatmap'>('trend')
  const { data, isLoading } = useQuery({
    queryKey: ['rotation-replay-minute', kind, bucket],
    queryFn: () => getJson<any>(`/api/rotation-replay?kind=${kind}&bucket=${bucket}`),
    staleTime: 300_000,
  })

  const timeline = data?.timeline ?? []
  const times: string[] = timeline.map((x: any) => x.time)
  const matrix = data?.series?.matrix ?? []
  const names: string[] = data?.series?.sectors ?? []
  const latest = timeline[timeline.length - 1]

  const trendOption = useMemo(() => ({
    animation: false,
    color: COLORS,
    grid: { left: 46, right: 18, top: 30, bottom: 30 },
    legend: { top: 0, type: 'scroll', textStyle: { color: '#94a3b8', fontSize: 10 } },
    tooltip: { trigger: 'axis', backgroundColor: '#111a2e', borderColor: '#334155', textStyle: { color: '#e2e8f0', fontSize: 11 },
      valueFormatter: (v: number | null) => v === null ? '—' : `${v.toFixed(2)}%` },
    xAxis: { type: 'category', data: times, axisLabel: { color: '#64748b', fontSize: 10 }, axisLine: { lineStyle: { color: '#334155' } } },
    yAxis: { type: 'value', axisLabel: { color: '#64748b', formatter: '{value}%' }, splitLine: { lineStyle: { color: '#1e293b' } } },
    series: matrix.map((row: any) => ({
      name: row.name, type: 'line', showSymbol: false, smooth: false,
      data: row.values.map((v: number | null) => v === null ? null : v * 100),
      lineStyle: { width: 1.6 }, emphasis: { focus: 'series', lineStyle: { width: 3 } },
    })),
  }), [times.join(','), matrix])

  const heatOption = useMemo(() => {
    const start = bucket === 1 ? Math.max(0, times.length - 60) : 0
    const heatTimes = times.slice(start)
    const cells = matrix.flatMap((row: any, y: number) => row.values.slice(start).flatMap((v: number | null, x: number) =>
      v === null ? [] : [[x, y, v * 100]]))
    return {
      animation: false,
      grid: { left: 82, right: 18, top: 12, bottom: 55 },
      tooltip: { backgroundColor: '#111a2e', borderColor: '#334155', textStyle: { color: '#e2e8f0' },
        formatter: (p: any) => `${names[p.value[1]]}<br/>${heatTimes[p.value[0]]} · ${p.value[2].toFixed(2)}%` },
      xAxis: { type: 'category', data: heatTimes, axisLabel: { color: '#64748b', fontSize: 9 }, splitArea: { show: false } },
      yAxis: { type: 'category', data: names, axisLabel: { color: '#94a3b8', fontSize: 10 } },
      visualMap: { min: -3, max: 3, calculable: false, orient: 'horizontal', left: 'center', bottom: 0,
        text: ['强', '弱'], textStyle: { color: '#94a3b8', fontSize: 10 },
        inRange: { color: ct.name === 'light'
          ? ['#059669', '#a7f3d0', '#f1f5f9', '#fecaca', '#ef4444']
          : ['#059669', '#134e4a', '#1e293b', '#7f1d1d', '#ef4444'] } },
      series: [{ type: 'heatmap', data: cells, itemStyle: { borderColor: ct.name === 'light' ? '#e2e8f0' : '#0f172a', borderWidth: 1 } }],
    }
  }, [times.join(','), matrix, names.join(','), bucket, ct])

  const rotationOption = useMemo(() => ({
    animation: false,
    grid: { left: 34, right: 10, top: 16, bottom: 26 },
    tooltip: { trigger: 'axis', backgroundColor: '#111a2e', borderColor: '#334155', textStyle: { color: '#e2e8f0', fontSize: 11 },
      formatter: (params: any) => {
        const row = timeline[params[0].dataIndex]
        return `${row.time}<br/>前十换血 ${(row.rotation * 100).toFixed(0)}%<br/>领涨 ${row.leader ?? '—'} ${fmtStockPct(row.leader_pct)}<br/>转强 ${row.cross_up} / 转弱 ${row.cross_down}`
      } },
    xAxis: { type: 'category', data: times, axisLabel: { color: '#64748b', fontSize: 9 }, axisLine: { lineStyle: { color: '#334155' } } },
    yAxis: { min: 0, max: 100, axisLabel: { color: '#64748b', formatter: '{value}%' }, splitLine: { lineStyle: { color: '#1e293b' } } },
    series: [{ type: 'line', data: timeline.map((x: any) => x.rotation * 100), showSymbol: false,
      lineStyle: { color: '#38bdf8', width: 2 }, areaStyle: { color: 'rgba(56,189,248,0.08)' } }],
  }), [timeline])

  if (isLoading) return <div className="rounded-xl border border-surface-700 bg-surface-850 p-8 text-center text-xs text-slate-500">正在聚合全市场分钟数据…</div>
  if (!data?.available) return <div className="rounded-xl border border-surface-700 bg-surface-850 p-8 text-center text-xs text-slate-500">暂无分钟回放数据</div>

  const changed = Math.round((latest?.rotation ?? 0) * 10)
  const pace = (latest?.rotation ?? 0) >= 0.5 ? '换血较快' : (latest?.rotation ?? 0) <= 0.2 ? '梯队较稳定' : '轮动中等'

  return (
    <section className="space-y-3 rounded-xl border border-surface-700 bg-surface-850 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold text-slate-100">{kind === 'concept' ? '概念' : '行业'}切换 · 盘中轮动</h2>
        <span className="rounded bg-surface-700 px-2 py-0.5 text-[10px] text-slate-300">分钟回放 · {data.date}</span>
        <div className="ml-auto flex flex-wrap gap-1">
          {(['concept', 'industry'] as const).map((k) => (
            <button key={k} onClick={() => setKind(k)} className={`rounded px-2 py-1 text-[11px] ${kind === k ? 'bg-brand/20 text-brand' : 'text-slate-400'}`}>{k === 'concept' ? '概念' : '行业'}</button>
          ))}
          {[1, 5, 15].map((b) => (
            <button key={b} onClick={() => setBucket(b)} className={`rounded px-2 py-1 text-[11px] ${bucket === b ? 'bg-brand/20 text-brand' : 'text-slate-400'}`}>{b}分钟</button>
          ))}
          <button onClick={() => setMode('trend')} className={`rounded px-2 py-1 text-[11px] ${mode === 'trend' ? 'bg-brand/20 text-brand' : 'text-slate-400'}`}>走势线</button>
          <button onClick={() => setMode('heatmap')} className={`rounded px-2 py-1 text-[11px] ${mode === 'heatmap' ? 'bg-brand/20 text-brand' : 'text-slate-400'}`}>热力图</button>
        </div>
      </div>

      <div className="rounded-lg bg-brand/5 px-3 py-2 text-xs leading-relaxed text-slate-300">
        当前领涨 <b className="text-brand">{latest?.leader ?? '—'}</b>（<b className={pctClass(latest?.leader_pct)}>{fmtStockPct(latest?.leader_pct)}</b>）；
        与约 1 小时前相比，涨幅前十换了 <b>{changed}</b> 个，{pace}。
      </div>

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_280px]">
        <div className="min-w-0 rounded-lg bg-surface-800/35 p-2">
          <div className="px-2 pt-1 text-[10px] text-slate-500">
            {mode === 'trend' ? '每条线=一个板块当天涨跌幅；看谁能持续走强，谁只是短暂冲高。' : '每格=该板块在这个时间桶的涨跌幅；红强绿弱。'}
          </div>
          <ReactECharts option={mode === 'trend' ? trendOption : heatOption} style={{ height: 390 }} notMerge />
        </div>
        <div className="min-w-0 space-y-2">
          <div className="rounded-lg bg-surface-800/35 p-2">
            <div className="px-2 text-[10px] text-slate-500">切换强度（当前前十 vs 约1小时前）</div>
            <ReactECharts option={rotationOption} style={{ height: 145 }} notMerge />
          </div>
          <div className="rounded-lg bg-surface-800/35 p-2">
            <div className="px-2 pb-1 text-[10px] text-slate-500">最新领涨榜 · 对比约1小时前</div>
            <div className="max-h-[205px] overflow-y-auto">
              {(data.sectors ?? []).map((s: any) => (
                <div key={s.name} className="flex items-center gap-2 rounded px-2 py-1 text-[11px] hover:bg-surface-700/50">
                  <span className="w-4 text-slate-600">{s.rank_now}</span>
                  <span className="min-w-0 flex-1 truncate text-slate-300">{s.name}</span>
                  <span className={`font-mono ${pctClass(s.pct_now)}`}>{fmtStockPct(s.pct_now)}</span>
                  <span className={`w-9 text-right text-[10px] ${s.rank_change > 0 ? 'text-rose-300' : s.rank_change < 0 ? 'text-emerald-300' : 'text-slate-600'}`}>
                    {s.rank_change > 0 ? `↑${s.rank_change}` : s.rank_change < 0 ? `↓${-s.rank_change}` : '—'}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="grid gap-2 md:grid-cols-2">
        <details className="rounded-lg bg-surface-800/35 p-3" open>
          <summary className="cursor-pointer text-[11px] text-slate-300">领涨易主路径（{data.leader_changes?.length ?? 0} 次）</summary>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {(data.leader_changes ?? []).map((c: any, i: number) => (
              <span key={i} className="rounded bg-surface-800 px-2 py-1 text-[10px] text-slate-400">{c.time} · {c.from} → <b className="text-brand">{c.to}</b></span>
            ))}
            {!data.leader_changes?.length && <span className="text-[10px] text-slate-600">全天未易主</span>}
          </div>
        </details>
        <details className="rounded-lg bg-surface-800/35 p-3">
          <summary className="cursor-pointer text-[11px] text-slate-300">0轴穿越事件（默认收起，点击展开）</summary>
          <div className="mt-2 max-h-36 space-y-1 overflow-y-auto">
            {(data.cross_events ?? []).slice(0, 30).map((c: any, i: number) => (
              <div key={i} className="text-[10px] text-slate-400">{c.time} · {c.name} · <span className={c.dir === 'up' ? 'text-rose-300' : 'text-emerald-300'}>{c.dir === 'up' ? '转强' : '转弱'}</span> {fmtStockPct(c.pct)}</div>
            ))}
          </div>
        </details>
      </div>
      <p className="text-[10px] leading-relaxed text-slate-600">{data.note}。这是历史分钟回放，不是今日实时，也不是买卖信号。</p>
    </section>
  )
}
