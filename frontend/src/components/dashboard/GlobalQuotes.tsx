import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import ReactECharts from '../ThemedECharts'
import { Globe, RefreshCw } from 'lucide-react'

import { api } from '../../lib/api'
import { fmtBigNum } from './format'
import { Modal } from '../Modal'
import { useChartTheme } from '../../lib/theme'

type QuoteItem = {
  symbol: string
  name?: string | null
  region?: string
  price?: number | null
  prev_close?: number | null
  change_pct?: number | null
  open?: number | null
  high?: number | null
  low?: number | null
  volume?: number | null
  amount?: number | null
  timestamp?: number | null
}

const PRESETS: Record<'US' | 'HK', { symbol: string; label: string }[]> = {
  // 指数走腾讯免费源（TickFlow 无指数，2026-11-07 拍板）；每 Tab 3 只一次请求
  US: [
    { symbol: 'DJI.US', label: '道琼斯' },
    { symbol: 'IXIC.US', label: '纳斯达克' },
    { symbol: 'INX.US', label: '标普500' },
  ],
  HK: [
    { symbol: 'HSI.HK', label: '恒生指数' },
    { symbol: 'HSTECH.HK', label: '恒生科技' },
    { symbol: 'HSCEI.HK', label: '国企指数' },
  ],
}

function pctCls(v?: number | null) {
  if (v == null) return 'text-slate-400'
  return v >= 0 ? 'text-rose-400' : 'text-emerald-400'
}
function fmtPct(v?: number | null) {
  return v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}
function fmtPrice(v?: number | null) {
  return v == null ? '—' : v.toFixed(2)
}
function fmtTime(ts?: number | null) {
  if (!ts) return '—'
  return new Date(ts).toLocaleString('zh-CN', { hour12: false })
}

/** 指数当日分时折线（颜色按最新价 vs 昨收红涨绿跌，虚线为昨收）。 */
function IntradayLine({ points, prevClose }: {
  points: { t: string; price: number }[]
  prevClose?: number | null
}) {
  const ct = useChartTheme()
  const last = points[points.length - 1]?.price
  const up = prevClose == null || last == null ? true : last >= prevClose
  const color = up ? '#f43f5e' : '#10b981'
  const option = {
    backgroundColor: 'transparent',
    grid: { left: 56, right: 16, top: 20, bottom: 30 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#16213a',
      borderColor: '#2a3a5f',
      textStyle: { color: '#e2e8f0', fontSize: 11 },
      formatter: (items: any) => {
        const it = items[0]
        return `${it.axisValue}<br/>价格 <b>${Number(it.data).toFixed(2)}</b>`
      },
    },
    xAxis: {
      type: 'category',
      data: points.map((p) => p.t),
      axisLabel: { color: '#94a3b8', fontSize: 10, interval: Math.ceil(points.length / 8) },
      axisLine: { lineStyle: { color: '#2a3a5f' } },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: { color: '#94a3b8', fontSize: 10 },
      splitLine: { lineStyle: { color: 'rgba(42,58,95,0.5)' } },
    },
    series: [
      {
        type: 'line',
        data: points.map((p) => p.price),
        showSymbol: false,
        lineStyle: { color, width: 1.5 },
        areaStyle: { color: up ? 'rgba(244,63,94,0.08)' : 'rgba(16,185,129,0.08)' },
        markLine: prevClose
          ? {
              symbol: 'none',
              label: { formatter: `昨收 ${prevClose}`, color: ct.name === 'light' ? ct.text : '#94a3b8', fontSize: 10 },
              lineStyle: { color: ct.name === 'light' ? ct.border : '#64748b', type: 'dashed', width: 1 },
              data: [{ yAxis: prevClose }],
            }
          : undefined,
      },
    ],
  }
  return <ReactECharts option={option} style={{ height: '100%' }} notMerge />
}

/** 单票/指数详情弹窗：实时快照（30s 刷新）+ 分时/日K切换（蜡烛图，红涨绿跌）。 */
export function GlobalQuoteDialog({ symbol, label, fallback, onClose }: {
  symbol: string
  label: string
  fallback?: QuoteItem
  onClose: () => void
}) {
  const snapQ = useQuery({
    queryKey: ['global-quote', symbol],
    queryFn: () => api.globalQuotes([symbol]),
    staleTime: 15_000,
    refetchInterval: 30_000,
    retry: 1,
  })
  const item: QuoteItem = (snapQ.data?.items?.[0] as QuoteItem) ?? fallback ?? { symbol }
  const [chartTab, setChartTab] = useState<'intraday' | 'daily'>('intraday')
  const intraQ = useQuery({
    queryKey: ['global-intraday', symbol],
    queryFn: () => api.globalIntraday(symbol),
    staleTime: 30_000,
    refetchInterval: chartTab === 'intraday' ? 60_000 : false,
    retry: 1,
  })
  const points = intraQ.data?.points ?? []
  const q = useQuery({
    queryKey: ['global-kline', symbol],
    queryFn: () => api.globalKline(symbol, 120),
    staleTime: 10 * 60_000,
    retry: 1,
    enabled: chartTab === 'daily',
  })
  const rows = q.data?.rows ?? []
  const dates = rows.map((r: any) => r.date)
  const option = {
    backgroundColor: 'transparent',
    grid: { left: 52, right: 14, top: 20, bottom: 30 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: '#16213a',
      borderColor: '#2a3a5f',
      textStyle: { color: '#e2e8f0', fontSize: 11 },
    },
    xAxis: {
      type: 'category',
      data: dates,
      axisLabel: { color: '#94a3b8', fontSize: 10 },
      axisLine: { lineStyle: { color: '#2a3a5f' } },
    },
    yAxis: {
      scale: true,
      axisLabel: { color: '#94a3b8', fontSize: 10 },
      splitLine: { lineStyle: { color: 'rgba(42,58,95,0.5)' } },
    },
    series: [
      {
        type: 'candlestick',
        data: rows.map((r: any) => [r.open, r.close, r.low, r.high]),
        itemStyle: {
          color: 'rgba(244,63,94,0.85)',
          color0: 'rgba(16,185,129,0.85)',
          borderColor: '#f43f5e',
          borderColor0: '#10b981',
        },
      },
    ],
  }
  const stats: [string, string][] = [
    ['今开', fmtPrice(item.open)],
    ['最高', fmtPrice(item.high)],
    ['最低', fmtPrice(item.low)],
    ['昨收', fmtPrice(item.prev_close)],
    ['成交量', item.volume == null ? '—' : fmtBigNum(item.volume)],
    ['成交额', item.amount == null ? '—' : fmtBigNum(item.amount)],
  ]
  return (
    <Modal
      onClose={onClose}
      labelledBy="global-quote-title"
      panelClassName="w-[94vw] max-w-2xl rounded-xl border border-surface-700 bg-surface-850 shadow-2xl"
    >
      <header className="flex items-center gap-3 border-b border-surface-700 p-4">
        <h2 id="global-quote-title" className="text-sm font-semibold text-slate-100">
          {label} <span className="ml-1 font-mono text-xs text-slate-400">{item.symbol}</span>
        </h2>
        <span className="font-mono text-lg text-slate-100">{fmtPrice(item.price)}</span>
        <span className={`font-mono text-sm ${pctCls(item.change_pct)}`}>
          {fmtPct(item.change_pct)}
        </span>
        <button
          onClick={onClose}
          className="ml-auto rounded-md px-2 py-1 text-slate-400 hover:bg-surface-700 hover:text-slate-200"
          aria-label="关闭"
        >
          ✕
        </button>
      </header>
      <div className="p-4">
        <div className="mb-3 grid grid-cols-3 gap-2 text-[11px] md:grid-cols-6">
          {stats.map(([k, v]) => (
            <div key={k} className="rounded-md bg-surface-800 px-2 py-1.5">
              <div className="text-slate-500">{k}</div>
              <div className="mt-0.5 font-mono text-slate-200">{v}</div>
            </div>
          ))}
        </div>
        <div className="mb-2 text-[11px] text-slate-500">数据时间：{fmtTime(item.timestamp)}</div>
        <div className="mb-2 inline-flex gap-1 rounded-lg bg-surface-800 p-0.5">
          {(['intraday', 'daily'] as const).map((k) => (
            <button
              key={k}
              onClick={() => setChartTab(k)}
              className={`rounded px-3 py-0.5 text-[11px] transition-colors ${
                chartTab === k ? 'bg-brand/20 text-brand' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {k === 'intraday' ? '分时' : '日K'}
            </button>
          ))}
        </div>
        {chartTab === 'intraday' ? (
          intraQ.isLoading ? (
            <div className="flex h-72 items-center justify-center text-[12px] text-slate-500">分时加载中…</div>
          ) : points.length === 0 ? (
            <div className="flex h-72 items-center justify-center text-[12px] text-slate-500">
              {intraQ.data?.error ?? '该指数暂无分时数据（美股指数分钟线上游暂缺，可切日K）'}
            </div>
          ) : (
            <div className="h-80">
              <IntradayLine points={points} prevClose={item.prev_close} />
            </div>
          )
        ) : q.isLoading ? (
          <div className="flex h-72 items-center justify-center text-[12px] text-slate-500">K线加载中…</div>
        ) : rows.length === 0 ? (
          <div className="flex h-72 items-center justify-center text-[12px] text-slate-500">
            {q.data?.error ?? '暂无K线数据'}
          </div>
        ) : (
          <div className="h-80">
            <ReactECharts option={option} style={{ height: '100%' }} notMerge />
          </div>
        )}
        <p className="mt-2 text-[10px] text-slate-600">指数：腾讯免费行情；个股：TickFlow（当前账号 Key）。量额按上游口径：A股量=手、额已换算元，港股额=港元；美股额口径不可靠不硬填</p>
      </div>
    </Modal>
  )
}

/** 看板「美股 / 港股」卡片：两组预设标的快照，60s 刷新，点行进详情弹窗。 */
export function GlobalQuotes() {
  const [tab, setTab] = useState<'US' | 'HK'>('US')
  const [dialog, setDialog] = useState<{ item: QuoteItem; label: string } | null>(null)
  const symbols = PRESETS[tab].map((p) => p.symbol)
  const q = useQuery({
    queryKey: ['global-quotes', tab],
    queryFn: () => api.globalQuotes(symbols),
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: 1,
  })
  const items = q.data?.items ?? []
  const labelOf = (sym: string) =>
    PRESETS[tab].find((p) => p.symbol === sym)?.label ?? sym

  return (
    <section className="rounded-xl border border-surface-700 bg-surface-850 p-3">
      <header className="mb-2 flex items-center gap-2">
        <Globe className="h-4 w-4 text-brand" />
        <h2 className="text-sm font-semibold text-slate-100">美股 / 港股</h2>
        <div className="flex gap-1 rounded-lg bg-surface-800 p-0.5">
          {(['US', 'HK'] as const).map((k) => (
            <button
              key={k}
              onClick={() => setTab(k)}
              className={`rounded px-2.5 py-0.5 text-[11px] transition-colors ${
                tab === k ? 'bg-brand/20 text-brand' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {k === 'US' ? '美股' : '港股'}
            </button>
          ))}
        </div>
        <button
          onClick={() => q.refetch()}
          disabled={q.isFetching}
          className="ml-auto inline-flex items-center gap-1 rounded-md border border-surface-700 bg-surface-800 px-2 py-1 text-[11px] text-slate-300 hover:text-slate-100 disabled:opacity-50"
        >
          <RefreshCw className={`h-3 w-3 ${q.isFetching ? 'animate-spin' : ''}`} />
          刷新
        </button>
      </header>

      {q.data?.error ? (
        <p className="py-3 text-[12px] text-slate-500">{q.data.error}</p>
      ) : items.length === 0 ? (
        <p className="py-3 text-[12px] text-slate-500">
          {q.isLoading ? '加载中…' : '暂无数据（需在数据源页配置 TickFlow Key）'}
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 xl:grid-cols-3">
          {items.map((it) => (
            <button
              key={it.symbol}
              onClick={() => setDialog({ item: it, label: it.name || labelOf(it.symbol) })}
              className="flex items-center gap-2 rounded-lg border border-surface-700 bg-surface-800 px-2.5 py-1.5 text-left transition-colors hover:border-brand/40 hover:bg-surface-750"
              title="点击查看详情与日K"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-[12px] font-medium text-slate-200">
                  {it.name || labelOf(it.symbol)}
                </div>
                <div className="font-mono text-[10px] text-slate-500">
                  {it.symbol}
                  {it.amount != null && <span className="ml-1">· 额{fmtBigNum(it.amount)}</span>}
                </div>
              </div>
              <div className="text-right">
                <div className="font-mono text-[13px] text-slate-100">{fmtPrice(it.price)}</div>
                <div className={`font-mono text-[11px] ${pctCls(it.change_pct)}`}>
                  {fmtPct(it.change_pct)}
                </div>
              </div>
            </button>
          ))}
        </div>
      )}
      {dialog && (
        <GlobalQuoteDialog
          symbol={dialog.item.symbol}
          label={dialog.label}
          fallback={dialog.item}
          onClose={() => setDialog(null)}
        />
      )}
    </section>
  )
}
