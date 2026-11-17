import { useState } from 'react'
import ReactECharts from 'echarts-for-react'
import type { KlineRow } from '../lib/types'

const UP = '#f43f5e' // A股惯例：红涨
const DOWN = '#10b981' // 绿跌
const SUB_BG = '#0b1424'

/** 主图叠加指标（可切换） */
const OVERLAYS = [
  { key: 'ma', label: '均线' },
  { key: 'boll', label: 'BOLL' },
  { key: 'none', label: '无' },
] as const

/** 副图（可切换） */
const SUBS = [
  { key: 'vol', label: '成交量' },
  { key: 'macd', label: 'MACD' },
  { key: 'kdj', label: 'KDJ' },
  { key: 'vr', label: '量比' },
] as const

type Overlay = (typeof OVERLAYS)[number]['key']
type Sub = (typeof SUBS)[number]['key']

function line(name: string, data: (number | null | undefined)[], color: string, width = 1) {
  return {
    name,
    type: 'line' as const,
    data,
    smooth: true,
    symbol: 'none',
    lineStyle: { width, color },
    itemStyle: { color },
  }
}

/**
 * 日K蜡烛图 + 主图叠加（均线 / BOLL）+ 可切换副图（成交量 / MACD / KDJ / 量比）。
 *
 * 指标由 astock-hub 本地计算（从 tickflow 移植，数值逐位一致），所以副图数据来自
 * 同一个 /api/kline 响应，不需要额外请求。
 */
export default function KlineChart({ rows }: { rows: KlineRow[] }) {
  const [overlay, setOverlay] = useState<Overlay>('ma')
  const [sub, setSub] = useState<Sub>('vol')

  const dates = rows.map((r) => r.date.slice(5))
  const hasOHLC = rows.length > 0 && 'open' in rows[0] && rows[0].open != null

  // 只有收盘序列（iFinD 兜底路径）：不给副图，避免出现空图
  if (!hasOHLC) {
    return (
      <ReactECharts
        option={plainOption(rows, dates)}
        style={{ height: 440 }}
        notMerge
        opts={{ renderer: 'canvas' }}
      />
    )
  }

  const candles = rows.map((r) => [r.open, r.close, r.low, r.high])
  const overlays = overlaySeries(overlay, rows)
  const subNeedsZero = sub === 'macd' || sub === 'vr'

  const option = {
    animation: false,
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: '#111a2e',
      borderColor: '#2a3a5f',
      textStyle: { color: '#e2e8f0', fontSize: 12 },
      valueFormatter: (v: unknown) => (v == null ? '—' : Number(v).toFixed(2)),
    },
    legend: {
      data: overlays.map((s) => s.name),
      textStyle: { color: '#94a3b8', fontSize: 11 },
      top: 0,
    },
    grid: [
      { left: 48, right: 16, top: 28, height: '52%' },
      { left: 48, right: 16, top: '76%', height: '14%' },
    ],
    xAxis: [
      {
        type: 'category',
        data: dates,
        gridIndex: 0,
        axisLine: { lineStyle: { color: '#2a3a5f' } },
        axisLabel: { color: '#64748b', fontSize: 11 },
      },
      {
        type: 'category',
        gridIndex: 1,
        data: dates,
        axisLine: { lineStyle: { color: '#2a3a5f' } },
        axisLabel: { show: false },
      },
    ],
    yAxis: [
      {
        scale: true,
        gridIndex: 0,
        splitLine: { lineStyle: { color: '#1e2c4a' } },
        axisLabel: { color: '#64748b', fontSize: 11 },
      },
      {
        gridIndex: 1,
        scale: !subNeedsZero,
        splitLine: { show: false },
        axisLabel: { color: '#475569', fontSize: 10 },
      },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 45, end: 100 },
      {
        type: 'slider',
        xAxisIndex: [0, 1],
        bottom: 2,
        height: 14,
        start: 45,
        end: 100,
        borderColor: '#2a3a5f',
        fillerColor: 'rgba(56,189,248,0.10)',
        textStyle: { color: '#64748b', fontSize: 10 },
      },
    ],
    series: [
      {
        name: '日K',
        type: 'candlestick',
        data: candles,
        itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
      },
      ...overlays,
      ...buildSub(sub, rows),
    ],
  }

  return (
    <div>
      <div className="mb-1 flex flex-wrap items-center gap-1.5 px-1 text-[11px]">
        <span className="text-slate-500">叠加</span>
        {OVERLAYS.map((o) => (
          <Toggle key={o.key} active={overlay === o.key} onClick={() => setOverlay(o.key)}>
            {o.label}
          </Toggle>
        ))}
        <span className="ml-2 text-slate-500">副图</span>
        {SUBS.map((s) => (
          <Toggle key={s.key} active={sub === s.key} onClick={() => setSub(s.key)}>
            {s.label}
          </Toggle>
        ))}
      </div>
      <ReactECharts option={option} style={{ height: 440 }} notMerge opts={{ renderer: 'canvas' }} />
    </div>
  )
}

/** 小按钮组（tab/切换用；分时/日K tab 复用） */
export function Toggle({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded border px-1.5 py-0.5 text-[10px] transition-colors ${
        active
          ? 'border-brand/50 bg-brand/15 text-brand'
          : 'border-surface-600 bg-surface-800 text-slate-400 hover:text-slate-200'
      }`}
    >
      {children}
    </button>
  )
}

/** 主图叠加序列 */
function overlaySeries(overlay: Overlay, rows: KlineRow[]) {
  const pick = (k: keyof KlineRow) => rows.map((r) => (r[k] as number | undefined) ?? null)
  if (overlay === 'ma') {
    return [
      line('MA5', pick('ma5'), '#38bdf8'),
      line('MA10', pick('ma10'), '#fbbf24'),
      line('MA20', pick('ma20'), '#c084fc'),
      line('MA60', pick('ma60'), '#94a3b8'),
    ]
  }
  if (overlay === 'boll') {
    return [
      line('BOLL上轨', pick('boll_upper'), '#f472b6'),
      line('BOLL中轨', pick('boll_mid'), '#a78bfa'),
      line('BOLL下轨', pick('boll_lower'), '#38bdf8'),
    ]
  }
  return []
}

/** 副图序列（都挂在第 2 个 grid 上） */
function buildSub(sub: Sub, rows: KlineRow[]) {
  const pick = (k: keyof KlineRow) => rows.map((r) => (r[k] as number | undefined) ?? null)
  const onSub = { xAxisIndex: 1, yAxisIndex: 1 }

  if (sub === 'macd') {
    return [
      {
        name: 'MACD',
        type: 'bar' as const,
        ...onSub,
        data: pick('macd_hist').map((v) => ({
          value: v,
          itemStyle: { color: (v ?? 0) >= 0 ? UP : DOWN, opacity: 0.7 },
        })),
      },
      { ...line('DIF', pick('macd_dif'), '#38bdf8'), ...onSub },
      { ...line('DEA', pick('macd_dea'), '#fbbf24'), ...onSub },
    ]
  }
  if (sub === 'kdj') {
    return [
      { ...line('K', pick('kdj_k'), '#38bdf8'), ...onSub },
      { ...line('D', pick('kdj_d'), '#fbbf24'), ...onSub },
      { ...line('J', pick('kdj_j'), '#f472b6'), ...onSub },
    ]
  }
  if (sub === 'vr') {
    return [
      {
        ...line('量比', pick('vol_ratio_5d'), '#22d3ee', 1.2),
        ...onSub,
        markLine: {
          symbol: 'none',
          silent: true,
          label: { color: '#64748b', fontSize: 10, formatter: '{c}' },
          lineStyle: { color: '#334155', type: 'dashed' as const },
          data: [{ yAxis: 1 }, { yAxis: 2 }],
        },
      },
    ]
  }
  // 成交量（默认）
  return [
    {
      name: '成交量',
      type: 'bar' as const,
      ...onSub,
      data: rows.map((r) => ({
        value: r.volume,
        itemStyle: { color: r.close >= r.open ? UP : DOWN, opacity: 0.55 },
      })),
    },
  ]
}

/** 只有收盘序列时的降级图（iFinD 兜底路径） */
function plainOption(rows: KlineRow[], dates: string[]) {
  return {
    animation: false,
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: SUB_BG,
      borderColor: '#2a3a5f',
      textStyle: { color: '#e2e8f0', fontSize: 12 },
      valueFormatter: (v: unknown) => (v == null ? '—' : Number(v).toFixed(2)),
    },
    legend: {
      data: ['收盘', 'MA5', 'MA10', 'MA20'],
      textStyle: { color: '#94a3b8', fontSize: 11 },
      top: 0,
    },
    grid: [{ left: 48, right: 16, top: 30, bottom: 28 }],
    xAxis: {
      type: 'category',
      data: dates,
      axisLine: { lineStyle: { color: '#2a3a5f' } },
      axisLabel: { color: '#64748b', fontSize: 11 },
    },
    yAxis: {
      scale: true,
      splitLine: { lineStyle: { color: '#1e2c4a' } },
      axisLabel: { color: '#64748b', fontSize: 11 },
    },
    dataZoom: [{ type: 'inside', start: 45, end: 100 }],
    series: [
      {
        name: '收盘',
        type: 'line',
        data: rows.map((r) => r.close),
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 1.5, color: '#38bdf8' },
        areaStyle: { color: 'rgba(56,189,248,0.10)' },
        itemStyle: { color: '#38bdf8' },
      },
      line('MA5', rows.map((r) => r.ma5 ?? null), '#0ea5e9'),
      line('MA10', rows.map((r) => r.ma10 ?? null), '#fbbf24'),
      line('MA20', rows.map((r) => r.ma20 ?? null), '#c084fc'),
    ],
  }
}
