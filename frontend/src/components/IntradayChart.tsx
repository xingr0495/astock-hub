import ReactECharts from 'echarts-for-react'

// 配色与 KlineChart 严格一致（A股惯例：红涨绿跌）
const UP = '#f43f5e'
const DOWN = '#10b981'
const AXIS = '#2a3a5f'
const SPLIT = '#1e2c4a'
const LABEL = '#64748b'
const AVG_LINE = '#fbbf24'

/** 分时点位：[hhmm, 价格, 每分钟成交量(手), 均价(VWAP)] */
export type IntradayPoint = [string, number, number, number]

/** 后端 /api/intraday 成功时 data.data 的形状 */
export interface IntradayPayload {
  symbol: string
  date: string
  preclose?: number | null
  points: IntradayPoint[]
  source: string
  is_today: boolean
}

/** "0930" → "09:30"（已是 HH:MM 或 3 位数字也兜底成形） */
function fmtTime(hhmm: string): string {
  const s = String(hhmm ?? '')
  if (s.includes(':')) return s
  if (s.length === 3) return `0${s.slice(0, 1)}:${s.slice(1)}`
  if (s.length >= 4) return `${s.slice(0, 2)}:${s.slice(2, 4)}`
  return s
}

function fmtVol(v: number): string {
  if (v >= 10000) return `${(v / 10000).toFixed(v >= 100000 ? 0 : 1)}万`
  return String(Math.round(v))
}

/**
 * 今日分时图：双 grid（上价格 55% + 下成交量 20%，总高 440 与 KlineChart 一致）。
 *
 * - 价格线按「末价 vs 昨收」整根红/绿；昨收灰虚 markLine（preclose 缺失省略）；
 * - 均价线一条（#fbbf24）；量柱按当分钟价 vs 昨收着色（opacity 0.55）；
 * - 双 y 轴：左价 / 右涨跌幅%，按 max(|pct|,0.5)*1.1 对称取 min/max 使两轴刻度
 *   一一对应；preclose 缺失时退化为单左轴（不画昨收线、不算涨跌幅轴）；
 * - x 轴 category（HHMM→HH:MM），午休 11:30→13:00 自然直连。
 */
export default function IntradayChart({ payload }: { payload: IntradayPayload }) {
  const points = payload.points ?? []
  const times = points.map((p) => fmtTime(p[0]))
  const prices = points.map((p) => p[1])
  const volumes = points.map((p) => Math.round(p[2]))
  const avgs = points.map((p) => p[3])

  // 昨收防御：preclose 理论上恒有（后端缺省兜底=首点价），null 时退化单轴并用首点价着色
  const preclose =
    typeof payload.preclose === 'number' && payload.preclose > 0 ? payload.preclose : null
  const ref = preclose ?? prices[0] ?? null
  const last = prices.length ? prices[prices.length - 1] : null
  const lineColor = ref != null && last != null && last >= ref ? UP : DOWN

  // 双轴对称边界：涨跌幅绝对值最大取 0.5% 兜底，再 *1.1 留边
  const bound =
    preclose != null && prices.length
      ? Math.max(
          0.5,
          ...prices.map((p) => Math.abs((p / preclose - 1) * 100)),
          ...avgs.filter((a): a is number => typeof a === 'number' && a > 0).map((a) => Math.abs((a / preclose - 1) * 100)),
        ) * 1.1
      : null

  const option = {
    animation: false,
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: '#111a2e',
      borderColor: AXIS,
      textStyle: { color: '#e2e8f0', fontSize: 12 },
      formatter: (params: unknown) => {
        const list = Array.isArray(params) ? (params as any[]) : [params as any]
        if (!list.length) return ''
        const title = String(list[0]?.axisValue ?? '')
        const rows = list.map((p) => {
          const name = String(p.seriesName ?? '')
          const v = Number(p.value)
          if (name === '成交量') return `${p.marker}${name}: ${v.toLocaleString('zh-CN')} 手`
          let row = `${p.marker}${name}: ${v.toFixed(2)}`
          if (name === '价格' && preclose != null) {
            const pct = (v / preclose - 1) * 100
            row += ` (${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%)`
          }
          return row
        })
        return `${title}<br/>${rows.join('<br/>')}`
      },
    },
    grid: [
      { left: 48, right: 56, top: 28, height: '55%' },
      { left: 48, right: 56, top: '74%', height: '20%' },
    ],
    xAxis: [
      {
        type: 'category',
        data: times,
        gridIndex: 0,
        boundaryGap: false,
        axisLine: { lineStyle: { color: AXIS } },
        axisLabel: { show: false },
      },
      {
        type: 'category',
        data: times,
        gridIndex: 1,
        boundaryGap: true,
        axisLine: { lineStyle: { color: AXIS } },
        axisLabel: { color: LABEL, fontSize: 11 },
      },
    ],
    yAxis: [
      // 左价轴：有昨收时显式 min/max 与右轴对称对齐；无昨收退化 scale 自适应
      {
        gridIndex: 0,
        type: 'value',
        scale: bound == null,
        splitNumber: 5,
        min: bound != null && preclose != null ? preclose * (1 - bound / 100) : undefined,
        max: bound != null && preclose != null ? preclose * (1 + bound / 100) : undefined,
        splitLine: { lineStyle: { color: SPLIT } },
        axisLabel: { color: LABEL, fontSize: 11, formatter: (v: number) => v.toFixed(2) },
      },
      // 右涨跌幅%轴：仅昨收存在时启用，与左轴同一 splitNumber 刻度对应
      ...(bound != null
        ? [
            {
              gridIndex: 0,
              type: 'value' as const,
              min: -bound,
              max: bound,
              splitNumber: 5,
              splitLine: { show: false },
              axisLabel: {
                color: LABEL,
                fontSize: 11,
                formatter: (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`,
              },
            },
          ]
        : []),
      // 量能轴（第 2 grid）
      {
        gridIndex: 1,
        type: 'value' as const,
        splitLine: { show: false },
        axisLabel: { color: '#475569', fontSize: 10, formatter: (v: number) => fmtVol(v) },
      },
    ],
    series: [
      {
        name: '价格',
        type: 'line' as const,
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: prices,
        symbol: 'none',
        lineStyle: { width: 1.2, color: lineColor },
        itemStyle: { color: lineColor },
        // 昨收灰虚线（preclose 缺失省略）
        ...(preclose != null
          ? {
              markLine: {
                symbol: 'none',
                silent: true,
                label: {
                  show: true,
                  formatter: preclose.toFixed(2),
                  color: LABEL,
                  fontSize: 10,
                  position: 'insideEndTop',
                },
                lineStyle: { color: '#64748b', type: 'dashed' as const, width: 1 },
                data: [{ yAxis: preclose }],
              },
            }
          : {}),
      },
      {
        name: '均价',
        type: 'line' as const,
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: avgs,
        symbol: 'none',
        lineStyle: { width: 1, color: AVG_LINE },
        itemStyle: { color: AVG_LINE },
      },
      {
        name: '成交量',
        type: 'bar' as const,
        xAxisIndex: 1,
        yAxisIndex: bound != null ? 2 : 1,
        data: volumes,
        itemStyle: {
          opacity: 0.55,
          color: (params: any) => {
            const p = prices[params?.dataIndex]
            if (p == null || ref == null) return UP
            return p >= ref ? UP : DOWN
          },
        },
      },
    ],
  }

  return (
    <div>
      <div className="mb-1 flex items-center gap-2 px-1 text-[11px]">
        <span className="text-slate-400">{payload.date} 分时</span>
        {payload.is_today ? (
          <span className="rounded bg-rose-500/15 px-1.5 py-0.5 text-[10px] text-rose-300">实时</span>
        ) : (
          <span className="text-slate-600">非今日</span>
        )}
      </div>
      <ReactECharts option={option} style={{ height: 440 }} notMerge opts={{ renderer: 'canvas' }} />
    </div>
  )
}
