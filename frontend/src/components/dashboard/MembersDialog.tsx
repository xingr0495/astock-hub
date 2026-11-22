import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { useNavigate } from "react-router-dom"

import { Modal } from "../Modal"
import { api } from "../../lib/api"
import { sessionRefreshMs, useMinuteTick } from "../../lib/refresh"
import { fmtStockPct, pctClass } from "./format"
import { Skeleton } from '../../components/Skeleton'
import { SectorIntradayChart } from './SectorIntradayChart'
import { LeaderScorePanel } from './LeaderScorePanel'

/** 板块成分股弹窗（concept/industry），带当日涨幅；点击个股跳分析页。
 *  盘中按 30s 节奏轮询实时涨幅（realtime=true，后端与主线实时帧共享 30s 缓存）。 */
export function MembersDialog({ kind, name, onClose }: {
  kind: "concept" | "industry"
  name: string | null
  onClose: () => void
}) {
  const navigate = useNavigate()
  const [showIntraday, setShowIntraday] = useState(false)
  const [showLeader, setShowLeader] = useState(false)
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
      panelClassName="w-[92vw] max-w-2xl rounded-xl border border-surface-700 bg-surface-850 shadow-2xl">
      <header className="flex items-center justify-between gap-2 border-b border-surface-700 p-3">
        <h2 id="members-title" className="min-w-0 text-sm font-semibold text-slate-100">
          {name}
          <span className="ml-1 text-xs font-normal text-slate-400">
            · 成分股 {q.data?.total ?? ""}（{stamp}，按涨幅排序）
          </span>
        </h2>
        <div className="flex shrink-0 items-center gap-1">
          <button onClick={() => setShowIntraday((v) => !v)}
            className={`rounded px-2 py-1 text-[11px] ${showIntraday ? 'bg-brand/20 text-brand' : 'text-slate-400 hover:bg-surface-700 hover:text-slate-200'}`}>
            {showIntraday ? '收起分时' : '板块分时'}
          </button>
          <button onClick={() => setShowLeader((v) => !v)}
            className={`rounded px-2 py-1 text-[11px] ${showLeader ? 'bg-brand/20 text-brand' : 'text-slate-400 hover:bg-surface-700 hover:text-slate-200'}`}>
            {showLeader ? '收起评分' : '龙头评分'}
          </button>
          <button onClick={onClose} aria-label="关闭成分股弹窗" className="rounded px-1.5 text-slate-400 hover:bg-surface-700 hover:text-slate-200">
            ✕
          </button>
        </div>
      </header>
      {showIntraday && name && (
        <div className="border-b border-surface-700 p-3">
          <SectorIntradayChart kind={kind} name={name} />
        </div>
      )}
      {showLeader && name && (
        <div className="max-h-[65vh] overflow-y-auto border-b border-surface-700 p-3">
          <LeaderScorePanel kind={kind} name={name} />
        </div>
      )}
      <div className="max-h-[60vh] overflow-auto p-2">
        {q.isLoading && (
          <div className="py-8 text-center text-xs text-slate-400"><Skeleton /></div>
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
