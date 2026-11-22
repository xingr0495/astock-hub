import { useQuery } from "@tanstack/react-query"
import ReactECharts from "../ThemedECharts"

import { api } from "../../lib/api"
import type { Envelope } from "../../lib/types"

/** 资金温度计：汇金宽基份额（12周）折线 + 各宽基份额 + 周环比。 */
export function FundThermometer() {
  const q = useQuery<Envelope>({
    queryKey: ["dash-nt-etf"],
    queryFn: () => api.ntEtf(12),
    staleTime: 2 * 60_000,
  })
  const etf = (q.data?.data as any)?.data as any
  const series = etf?.series ?? []

  const spark = {
    backgroundColor: "transparent",
    grid: { left: 4, right: 4, top: 8, bottom: 4 },
    xAxis: { type: "category", show: false, data: series.map((s: any) => s.date) },
    yAxis: { type: "value", show: false, scale: true },
    series: [
      {
        type: "line",
        data: series.map((s: any) => s["全宽基合计"]),
        smooth: true,
        symbol: "none",
        lineStyle: { color: "#38bdf8", width: 1.5 },
        areaStyle: { color: "rgba(56,189,248,0.12)" },
      },
    ],
  }

  return (
    <section className="rounded-xl border border-surface-700 bg-surface-850 p-4">
      <header className="mb-2 flex items-center gap-2">
        <h2 className="text-sm font-semibold text-slate-100">资金温度计</h2>
        <span className="ml-auto rounded bg-surface-700 px-2 py-0.5 text-[11px] text-slate-400">
          汇金宽基份额
        </span>
      </header>
      <div className="h-16">
        <ReactECharts option={spark} style={{ height: "100%" }} notMerge />
      </div>
      {etf?.latest && (
        <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-slate-400">
          {(["上证50", "沪深300", "中证500", "中证1000", "科创50", "中证A500"] as const).map((k) => (
            <div key={k} className="flex justify-between">
              <span>{k}</span>
              <b className="text-slate-200">{Math.round(etf.latest[k] ?? 0)}亿</b>
            </div>
          ))}
        </div>
      )}
      <div className="mt-2 text-[11px] text-slate-500">
        合计 {(etf?.latest?.["全宽基合计"] ?? 0).toFixed(0)} 亿份 · 周环比{" "}
        <span className={(etf?.week_over_week_total ?? 0) >= 0 ? "text-rose-400" : "text-emerald-400"}>
          {(etf?.week_over_week_total ?? 0) >= 0 ? "+" : ""}
          {(etf?.week_over_week_total ?? 0).toFixed(1)} 亿份
        </span>
      </div>
    </section>
  )
}
