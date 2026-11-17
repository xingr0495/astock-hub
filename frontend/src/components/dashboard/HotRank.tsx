import { useNavigate } from "react-router-dom"
import type { DashRank, DashRankItem } from "./types"
import { fmtStockPct, pctClass } from "./format"

function RankColumn({ title, rows, tone, onShowMembers, onPickStock }: {
  title: string
  rows: DashRankItem[]
  tone: "bull" | "bear"
  onShowMembers: (name: string) => void
  onPickStock: (symbol: string) => void
}) {
  return (
    <div className="min-w-0 space-y-1">
      <div className={`text-[10px] font-medium ${tone === "bull" ? "text-rose-400" : "text-emerald-400"}`}>
        {title}
      </div>
      {rows.slice(0, 5).map((r, idx) => {
        const hasLeader = !!r.leader?.symbol
        return (
          <div
            key={`${title}-${r.name}-${idx}`}
            onClick={() => onShowMembers(r.name)}
            className="grid cursor-pointer grid-cols-[14px_1fr_auto] items-center gap-1 rounded-md border border-transparent bg-surface-800/60 px-1.5 py-1 transition-colors hover:border-brand/40 hover:bg-surface-800"
          >
            <span className="text-center font-mono text-[9px] text-slate-400">{idx + 1}</span>
            <div className="min-w-0">
              <div className="truncate text-[11px] text-slate-100" title={r.name}>
                {r.name}
                <span className="ml-1 text-[8px] text-slate-500">↗</span>
              </div>
              <div className="mt-0.5 flex items-center gap-1">
                <span className="shrink-0 font-mono text-[9px] text-slate-400">{r.count}只</span>
                <span className="text-slate-500">·</span>
                {hasLeader ? (
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      onPickStock(r.leader.symbol!)
                    }}
                    className="truncate text-[10px] font-medium text-slate-300 hover:text-brand"
                  >
                    {r.leader.name ?? "—"}
                  </button>
                ) : (
                  <span className="truncate text-[10px] text-slate-400">{r.leader.name ?? "—"}</span>
                )}
                {r.leader?.change_pct != null && (
                  <span className={`shrink-0 font-mono text-[9px] ${pctClass(r.leader.change_pct)}`}>
                    {fmtStockPct(r.leader.change_pct)}
                  </span>
                )}
              </div>
            </div>
            <div className={`font-mono text-[10px] font-semibold ${pctClass(r.avg_pct)}`}>
              {fmtStockPct(r.avg_pct)}
            </div>
          </div>
        )
      })}
      {rows.length === 0 && (
        <div className="rounded border border-dashed border-surface-700 py-4 text-center text-xs text-slate-400">
          暂无数据
        </div>
      )}
    </div>
  )
}

/** 概念/行业热度卡：领涨/领跌，板块行看成分、领涨股跳个股。 */
export function HotRank({ title, rank, onShowMembers }: {
  title: string
  rank: DashRank
  onShowMembers: (name: string) => void
}) {
  const navigate = useNavigate()
  const pick = (symbol: string) => navigate(`/?code=${symbol}`)
  return (
    <section className="rounded-xl border border-surface-700 bg-surface-850 p-1.5 transition-shadow">
      <div className="mb-2 flex items-center gap-1.5">
        <span className="text-sm">🔥</span>
        <h3 className="text-xs font-semibold text-slate-100">{title}</h3>
        <span className="font-mono text-[10px] text-slate-400">领涨/领跌 · 点击板块看成分</span>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <RankColumn title="领涨" rows={rank.leading} tone="bull" onShowMembers={onShowMembers} onPickStock={pick} />
        <RankColumn title="领跌" rows={rank.lagging} tone="bear" onShowMembers={onShowMembers} onPickStock={pick} />
      </div>
    </section>
  )
}
