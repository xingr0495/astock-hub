import { useQuery } from "@tanstack/react-query"
import { useNavigate } from "react-router-dom"

import { Modal } from "../Modal"
import { api } from "../../lib/api"
import { sessionRefreshMs, useMinuteTick } from "../../lib/refresh"
import { fmtStockPct, pctClass } from "./format"

/** 板块成分股弹窗（concept/industry），带当日涨幅；点击个股跳分析页。
 *  盘中按 30s 节奏轮询实时涨幅（realtime=true，后端与主线实时帧共享 30s 缓存）。 */
export function MembersDialog({ kind, name, onClose }: {
  kind: "concept" | "industry"
  name: string | null
  onClose: () => void
}) {
  const navigate = useNavigate()
  useMinuteTick() // 让 9:15/15:05 时段边界的刷新节奏切换随时间生效
  const q = useQuery({
    queryKey: ["dashboard-members", kind, name],
    queryFn: () => api.dashboardMembers(kind, name!, true),
    enabled: !!name,
    refetchInterval: () => sessionRefreshMs(30_000),
  })
  const intraday = q.data?.intraday
  const stamp = intraday
    ? `盘中实时 ${q.data?.rt_time?.slice(11, 16) ?? ""}`
    : q.data?.as_of ?? ""

  return (
    <Modal onClose={onClose} labelledBy="members-title"
      panelClassName="w-[92vw] max-w-lg rounded-xl border border-surface-700 bg-surface-850 shadow-2xl">
      <header className="flex items-center justify-between border-b border-surface-700 p-3">
        <h2 id="members-title" className="text-sm font-semibold text-slate-100">
          {name}
          <span className="ml-1 text-xs font-normal text-slate-400">
            · 成分股 {q.data?.total ?? ""}（{stamp}，按涨幅排序）
          </span>
        </h2>
        <button onClick={onClose} className="rounded px-1.5 text-slate-400 hover:bg-surface-700 hover:text-slate-200">
          ✕
        </button>
      </header>
      <div className="max-h-[60vh] overflow-auto p-2">
        {q.isLoading && (
          <div className="py-8 text-center text-xs text-slate-400">加载中…</div>
        )}
        <div className="grid grid-cols-1 gap-1">
          {q.data?.rows.map((r) => (
            <button
              key={r.symbol}
              onClick={() => {
                onClose()
                navigate(`/?code=${r.symbol}`)
              }}
              className="flex items-center justify-between gap-2 rounded-md bg-surface-800/60 px-2.5 py-1.5 text-xs text-slate-200 transition-colors hover:bg-surface-800"
            >
              <span className="min-w-0 truncate">{r.name || r.symbol}</span>
              <span className="flex shrink-0 items-center gap-2">
                <span className={`font-mono text-[11px] font-semibold ${pctClass(r.change_pct)}`}>
                  {fmtStockPct(r.change_pct)}
                </span>
                <span className="font-mono text-[10px] text-slate-500">{r.symbol}</span>
              </span>
            </button>
          ))}
        </div>
      </div>
    </Modal>
  )
}
