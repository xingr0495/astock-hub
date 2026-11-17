import type { ReactNode } from "react"
import { useFlash } from "./format"

/** KPI 单元 + 小型指标（顶部条/趋势卡用）。onClick 存在时可点击（如成交额看历史）。 */
export function KpiCell({ label, value, sub, tone = "neutral", onClick, title }: {
  label: ReactNode
  value: ReactNode
  sub?: string
  tone?: "bull" | "bear" | "accent" | "neutral"
  onClick?: () => void
  title?: string
}) {
  const isPlain = typeof value === "string" || typeof value === "number"
  const flash = useFlash(isPlain ? (value as string | number) : undefined)
  const color =
    tone === "bull" ? "text-rose-400"
    : tone === "bear" ? "text-emerald-400"
    : tone === "accent" ? "text-brand"
    : "text-slate-100"
  return (
    <div
      className={`min-w-0 overflow-hidden rounded-lg border border-surface-700 bg-surface-850 px-2 py-1 transition-all hover:border-brand/30 ${
        onClick ? "cursor-pointer hover:bg-surface-800" : ""
      }`}
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      title={title}
      onKeyDown={
        onClick
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault()
                onClick()
              }
            }
          : undefined
      }
    >
      <div className="flex items-center gap-1 text-[11px] text-slate-400">{label}</div>
      <div className={`mt-1 truncate font-mono text-lg font-semibold leading-none ${isPlain ? color : "text-slate-100"} ${flash}`}>
        {value}
      </div>
      {sub && <div className="mt-1 truncate text-[10px] text-slate-400">{sub}</div>}
    </div>
  )
}

export function MiniMetric({ label, value, cls = "text-slate-100" }: {
  label: string
  value: string
  cls?: string
}) {
  return (
    <div className="rounded-md border border-surface-700/50 bg-surface-800 px-2 py-1.5">
      <div className="text-[10px] text-slate-400">{label}</div>
      <div className={`mt-0.5 font-mono text-xs font-semibold ${cls}`}>{value}</div>
    </div>
  )
}
