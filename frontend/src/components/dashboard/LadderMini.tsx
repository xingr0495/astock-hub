import { useNavigate } from "react-router-dom"

import type { DashLimit } from "./types"

/** 涨停梯队：每档显示全部个股（名称可点击跳个股页）。 */
export function LadderMini({ limit }: { limit: DashLimit }) {
  const navigate = useNavigate()
  const tiers = limit.tiers.filter((t) => t.boards >= 2).slice(0, 6)
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between rounded bg-surface-800 px-2 py-1.5 text-[11px]">
        <span className="text-slate-400">封板率</span>
        <span className="font-mono text-brand">{(limit.seal_rate ?? 0).toFixed(0)}%</span>
      </div>
      {tiers.length === 0 && (
        <div className="rounded border border-dashed border-surface-700 py-5 text-center text-xs text-slate-400">
          暂无 2 板以上
        </div>
      )}
      {tiers.map((t) => (
        <div key={t.boards} className="rounded bg-surface-800/60 px-2 py-1.5">
          <div className="grid grid-cols-[42px_1fr_auto] items-center gap-2">
            <span className={`font-mono text-sm font-bold ${t.boards >= 5 ? "text-rose-400" : t.boards >= 3 ? "text-brand" : "text-slate-300"}`}>
              {t.boards}板
            </span>
            <div className="h-1.5 overflow-hidden rounded-full bg-surface-700">
              <div className="h-full rounded-full bg-rose-500/70" style={{ width: `${Math.min(100, t.count * 12)}%` }} />
            </div>
            <span className="font-mono text-xs text-slate-100">{t.count}</span>
          </div>
          <div className="mt-1 flex flex-wrap gap-1 pl-[50px]">
            {t.stocks.map((s) => (
              <button
                key={s.symbol}
                onClick={() => navigate(`/?code=${s.symbol}`)}
                className="rounded bg-surface-700/50 px-1.5 py-0.5 text-[9px] text-slate-300 transition-colors hover:bg-brand/20 hover:text-brand"
                title={s.symbol}
              >
                {s.name || s.symbol}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
