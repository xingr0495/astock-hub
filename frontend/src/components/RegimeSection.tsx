import { useQuery } from '@tanstack/react-query'
import ReactECharts from './ThemedECharts'
import { api } from '../lib/api'
import { useChartTheme } from '../lib/theme'

const STATE_COLOR: Record<string, string> = {
  strong: '#f43f5e',
  lean_strong: '#fb7185',
  range: '#94a3b8',
  lean_weak: '#34d399',
  weak: '#10b981',
}

const PHASE_LABEL: Record<string, string> = {
  ice: '❄️ 冰点',
  ignite: '🔥 启动',
  rally: '🚀 主升',
  climax: '⚠️ 高潮',
  ebb: '🌊 退潮',
  repair: '🔧 修复',
}

/**
 * 市场情绪周期：情绪分（0-100）+ 5 档状态 + 6 阶段 + 4 个子维度分 + 历史走势。
 * 本地计算（同花顺全市场日线），不依赖 tickflow 服务。
 */
export default function RegimeSection() {
  const ct = useChartTheme()
  const query = useQuery({
    queryKey: ['regime', 60],
    queryFn: () => api.regime(60),
    staleTime: 5 * 60_000,
  })

  if (query.isLoading) {
    return <div className="py-10 text-center text-xs text-slate-500">正在计算市场情绪周期（全市场）…</div>
  }
  const snap = query.data?.data as any
  if (!snap?.latest) {
    return (
      <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-xs text-amber-300">
        情绪周期暂不可用：{String((query.data?.data as any)?.data?.hint ?? query.error ?? '')}
      </div>
    )
  }

  const latest = snap.latest
  const history: any[] = snap.history ?? []
  const stateColor = STATE_COLOR[latest.state] ?? '#94a3b8'

  const gauge = {
    series: [
      {
        type: 'gauge',
        startAngle: 200,
        endAngle: -20,
        min: 0,
        max: 100,
        radius: '92%',
        center: ['50%', '62%'],
        progress: { show: true, width: 10, itemStyle: { color: stateColor } },
        axisLine: { lineStyle: { width: 10, color: [[1, ct.grid]] } },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
        pointer: { show: false },
        anchor: { show: false },
        detail: {
          valueAnimation: false,
          offsetCenter: [0, '-10%'],
          formatter: () => `{a|${latest.score}}\n{b|${snap.state_label}}`,
          rich: {
            a: { fontSize: 30, fontWeight: 'bold', color: ct.name === 'light' ? ct.textStrong : '#f1f5f9', lineHeight: 38 },
            b: { fontSize: 12, color: stateColor, lineHeight: 20 },
          },
        },
        data: [{ value: latest.score }],
      },
    ],
  }

  const histOption = {
    animation: false,
    grid: { left: 34, right: 12, top: 24, bottom: 22 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#111a2e',
      borderColor: '#2a3a5f',
      textStyle: { color: '#e2e8f0', fontSize: 11 },
      formatter: (params: any) => {
        const p = params[0]
        const row = history[p.dataIndex]
        return `${p.axisValue}<br/>情绪分 <b>${row.score}</b>（${snap.state_label === undefined ? row.state : ''}）<br/>` +
          `涨停 ${row.limit_up} · 连板高 ${row.max_consecutive}<br/>阶段：${PHASE_LABEL[row.phase] ?? row.phase}`
      },
    },
    legend: {
      data: ['情绪分'],
      textStyle: { color: '#94a3b8', fontSize: 10 },
      top: 0,
    },
    xAxis: {
      type: 'category',
      data: history.map((h) => String(h.date).slice(5)),
      axisLine: { lineStyle: { color: '#2a3a5f' } },
      axisLabel: { color: '#64748b', fontSize: 10 },
    },
    yAxis: {
      min: 0,
      max: 100,
      splitLine: { lineStyle: { color: '#1e2c4a' } },
      axisLabel: { color: '#64748b', fontSize: 10 },
    },
    series: [
      {
        name: '情绪分',
        type: 'line',
        data: history.map((h) => ({
          value: h.score,
          itemStyle: { color: STATE_COLOR[h.state] ?? '#94a3b8' },
        })),
        symbol: 'circle',
        symbolSize: 5,
        lineStyle: { width: 1.6, color: '#38bdf8' },
        markLine: {
          symbol: 'none',
          silent: true,
          label: { color: '#64748b', fontSize: 9 },
          lineStyle: { color: '#334155', type: 'dashed' },
          data: [
            { yAxis: 70, label: { formatter: '强势' } },
            { yAxis: 30, label: { formatter: '弱势' } },
          ],
        },
      },
    ],
  }

  const subScores = [
    ['赚钱', latest.profit_score],
    ['投机', latest.speculation_score],
    ['抗跌', latest.resilience_score],
    ['趋势', latest.trend_score],
  ] as const

  return (
    <section className="rounded-xl border border-surface-700 bg-surface-850 p-4">
      <header className="mb-2 flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold text-slate-100">市场情绪周期</h2>
        <span className="rounded bg-surface-700 px-2 py-0.5 text-[10px] text-slate-300">
          {PHASE_LABEL[latest.phase] ?? latest.phase}
        </span>
        <span className="ml-auto text-[10px] text-slate-500">
          全市场日线 · 本地计算
        </span>
      </header>

      <div className="grid gap-3 md:grid-cols-3">
        <div className="h-40">
          <ReactECharts option={gauge} style={{ height: '100%' }} notMerge />
        </div>
        <div className="h-40 md:col-span-2">
          <ReactECharts option={histOption} style={{ height: '100%' }} notMerge />
        </div>
      </div>

      <div className="mt-2 grid grid-cols-4 gap-2">
        {subScores.map(([k, v]) => (
          <div key={k} className="rounded-lg bg-surface-700/50 px-2 py-1.5 text-center">
            <div className="text-[10px] text-slate-500">{k}</div>
            <div className="text-sm font-semibold text-slate-100">{v ?? '—'}</div>
          </div>
        ))}
      </div>

      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
        <span>涨停 {latest.limit_up} · 跌停 {latest.limit_down}</span>
        <span>最高连板 {latest.max_consecutive} · 首板 {latest.first_board ?? '—'}</span>
        <span>MA20 上方 {(latest.above_ma20_pct * 100).toFixed(0)}%</span>
        {snap.transition && (
          <span className="text-brand">
            最近阶段切换 {String(snap.transition[0]).slice(5)}：
            {PHASE_LABEL[snap.transition[1]] ?? snap.transition[1]} →{' '}
            {PHASE_LABEL[snap.transition[2]] ?? snap.transition[2]}
          </span>
        )}
      </div>
    </section>
  )
}
