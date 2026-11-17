import { ArrowDownRight, ArrowUpRight } from "lucide-react"
import type { DashIndex } from "./types"
import { fmtIndexPct, fmtPrice, pctClass } from "./format"

/** 单个核心指数条（可点击查看实时行情：快照 + 分时/日K）。 */
export function IndexTicker({ item, onSelect }: {
  item: DashIndex
  onSelect?: (item: DashIndex) => void
}) {
  const pct = item.change_pct
  const isUp = (pct ?? 0) >= 0
  return (
    <button
      type="button"
      onClick={() => onSelect?.(item)}
      title="点击查看实时行情"
      className="grid min-w-0 grid-cols-[1fr_auto] items-center gap-x-2 gap-y-0.5 rounded-lg border border-surface-700 bg-surface-800 px-1.5 py-1 text-left transition-colors hover:border-brand/40"
    >
      <div className="truncate text-xs font-medium text-slate-100">{item.name}</div>
      <div className={`font-mono text-xs font-semibold ${pctClass(pct)}`}>
        {fmtIndexPct(pct)}
      </div>
      <div className="font-mono text-[10px] text-slate-500">{item.symbol}</div>
      <div className={`flex items-center gap-1 font-mono text-[11px] ${pctClass(pct)}`}>
        {isUp ? <ArrowUpRight className="h-3 w-3" /> : <ArrowDownRight className="h-3 w-3" />}
        {fmtPrice(item.last_price)}
      </div>
    </button>
  )
}
