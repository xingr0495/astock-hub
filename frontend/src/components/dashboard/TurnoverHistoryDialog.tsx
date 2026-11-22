import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import ReactECharts from '../ThemedECharts'

import { api } from '../../lib/api'
import { fmtBigNum } from './format'
import { Modal } from '../Modal'
import { useChartTheme } from '../../lib/theme'

type Point = { date: string; total: number; count?: number }

interface Props {
  onClose: () => void
  /** 盘中实时成交额（仅实时看板传入；历史回看不传），作为最后一根异色柱。 */
  liveToday?: Point
}

const RANGES = [20, 60, 120] as const

function mmdd(d: string) {
  return d.slice(5)
}

/** 看板「成交额」KPI 点击弹窗：近 N 个交易日全市场成交额柱状图（本地仓库，A 股口径）。 */
export function TurnoverHistoryDialog({ onClose, liveToday }: Props) {
  const ct = useChartTheme()
  const [days, setDays] = useState<number>(60)
  const q = useQuery({
    queryKey: ['amount-history', days],
    queryFn: () => api.amountHistory(days),
    staleTime: 10 * 60_000,
  })

  const payload = q.data?.data as
    | { series?: Point[]; hint?: string; status?: string }
    | undefined
  const series = useMemo<Point[]>(() => {
    const hist = payload?.series ?? []
    if (!liveToday || hist.some((p) => p.date === liveToday.date)) return hist
    return [...hist, liveToday]
  }, [payload, liveToday])

  const liveDates = useMemo(
    () => new Set(liveToday ? [liveToday.date] : []),
    [liveToday],
  )
  const avg = series.length
    ? series.reduce((s, p) => s + p.total, 0) / series.length
    : null
  const latest = series[series.length - 1]

  const option = useMemo(
    () => ({
      backgroundColor: 'transparent',
      grid: { left: 48, right: 12, top: 28, bottom: 28 },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        backgroundColor: '#16213a',
        borderColor: '#2a3a5f',
        textStyle: { color: '#e2e8f0', fontSize: 11 },
        formatter: (items: any) => {
          const it = items[0]
          const p = series[it.dataIndex]
          const tag = liveDates.has(p.date) ? '（盘中实时）' : ''
          return `${p.date}${tag}<br/>成交额 <b>${fmtBigNum(p.total)}</b>`
        },
      },
      xAxis: {
        type: 'category',
        data: series.map((p) => mmdd(p.date)),
        axisLabel: { color: '#94a3b8', fontSize: 10, interval: 'auto' },
        axisLine: { lineStyle: { color: '#2a3a5f' } },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: {
          color: '#94a3b8',
          fontSize: 10,
          formatter: (v: number) => `${(v / 1e8).toFixed(0)}亿`,
        },
        splitLine: { lineStyle: { color: 'rgba(42,58,95,0.5)' } },
      },
      series: [
        {
          type: 'bar',
          data: series.map((p) => ({
            value: p.total,
            itemStyle: { color: liveDates.has(p.date) ? '#f59e0b' : '#38bdf8' },
          })),
          barMaxWidth: 14,
          markLine: avg
            ? {
                symbol: 'none',
                label: {
                  formatter: `均值 ${fmtBigNum(avg)}`,
                  color: ct.name === 'light' ? ct.text : '#94a3b8',
                  fontSize: 10,
                  position: 'insideEndTop',
                },
                lineStyle: { color: ct.name === 'light' ? ct.border : '#64748b', type: 'dashed', width: 1 },
                data: [{ yAxis: avg }],
              }
            : undefined,
        },
      ],
    }),
    [series, avg, liveDates, ct],
  )

  return (
    <Modal
      onClose={onClose}
      labelledBy="turnover-history-title"
      panelClassName="w-[94vw] max-w-2xl rounded-xl border border-surface-700 bg-surface-850 shadow-2xl"
    >
      <header className="flex flex-wrap items-center gap-3 border-b border-surface-700 p-4">
        <h2 id="turnover-history-title" className="text-sm font-semibold text-slate-100">
          历史成交额（全市场 A 股）
        </h2>
        <div className="flex gap-1 rounded-lg bg-surface-800 p-0.5">
          {RANGES.map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`rounded px-2.5 py-0.5 text-[11px] transition-colors ${
                days === d
                  ? 'bg-brand/20 text-brand'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {d}日
            </button>
          ))}
        </div>
        <button
          onClick={onClose}
          className="ml-auto rounded-md px-2 py-1 text-slate-400 hover:bg-surface-700 hover:text-slate-200"
          aria-label="关闭"
        >
          ✕
        </button>
      </header>

      <div className="p-4">
        {latest && (
          <div className="mb-2 flex gap-4 text-[11px] text-slate-400">
            <span>
              最新（{latest.date}
              {liveDates.has(latest.date) ? ' · 盘中' : ''}）
              <b className="ml-1 font-mono text-slate-100">{fmtBigNum(latest.total)}</b>
            </span>
            {avg && (
              <span>
                近{series.length}日均值
                <b className="ml-1 font-mono text-slate-100">{fmtBigNum(avg)}</b>
              </span>
            )}
          </div>
        )}

        {q.isLoading ? (
          <div className="flex h-72 items-center justify-center text-[12px] text-slate-500">
            加载中…
          </div>
        ) : series.length === 0 ? (
          <div className="flex h-72 items-center justify-center text-[12px] text-slate-500">
            {payload?.hint ?? '本地暂无历史成交额数据（盘后数据链落库后可看）。'}
          </div>
        ) : (
          <div className="h-80">
            <ReactECharts option={option} style={{ height: '100%' }} notMerge />
          </div>
        )}
        <p className="mt-2 text-[10px] text-slate-600">
          口径与看板成交额一致（A 股白名单，不含 ETF/债券/指数）；琥珀色柱为盘中实时值，
          收盘后以数据链落库值为准。
        </p>
      </div>
    </Modal>
  )
}
