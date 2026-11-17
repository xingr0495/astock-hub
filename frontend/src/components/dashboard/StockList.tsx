import { useNavigate } from "react-router-dom"
import type { DashRow } from "./types"
import { fmtBigNum, fmtPrice, fmtStockPct, pctClass } from "./format"

/** 四大榜单（涨幅/跌幅/成交额/活跃换手），点击行跳个股页。 */
export function StockList({ title, rows, mode }: {
  title: string
  rows: DashRow[]
  mode: "gain" | "loss" | "amount" | "active"
}) {
  const navigate = useNavigate()
  return (
    <div className="rounded-xl border border-surface-700 bg-surface-850 p-1.5 transition-shadow">
      <div className="mb-1 flex items-center justify-between">
        <h3 className="text-xs font-semibold text-slate-100">{title}</h3>
        <span className="text-[9px] text-slate-400">TOP {Math.min(rows.length, 8)}</span>
      </div>
      <div className="space-y-1">
        {rows.slice(0, 8).map((r, idx) => (
          <div
            key={`${r.symbol}-${idx}`}
            onClick={() => navigate(`/?code=${r.symbol}`)}
            className="grid cursor-pointer grid-cols-[18px_1fr_auto] items-center gap-1.5 rounded-md border border-transparent bg-surface-800/60 px-1.5 py-1 transition-colors hover:border-surface-600 hover:bg-surface-800"
          >
            <span className="text-center font-mono text-[10px] text-slate-400">{idx + 1}</span>
            <div className="min-w-0">
              <span className="truncate text-[11px] text-slate-100">{r.name || r.symbol}</span>
              <div className="font-mono text-[9px] text-slate-500">{r.symbol}</div>
            </div>
            <div className="text-right">
              {mode === "amount" ? (
                <>
                  <div className="font-mono text-[11px] text-slate-100">{fmtBigNum(r.amount)}</div>
                  <div className={`font-mono text-[9px] ${pctClass(r.change_pct)}`}>{fmtStockPct(r.change_pct)}</div>
                </>
              ) : mode === "active" ? (
                <>
                  <div className="font-mono text-[11px] text-brand">{fmtPrice(r.turnover_rate, 1)}%</div>
                  <div className={`font-mono text-[9px] ${pctClass(r.change_pct)}`}>{fmtStockPct(r.change_pct)}</div>
                </>
              ) : (
                <>
                  <div className={`font-mono text-[11px] font-semibold ${pctClass(r.change_pct)}`}>{fmtStockPct(r.change_pct)}</div>
                  <div className="font-mono text-[9px] text-slate-400">{fmtPrice(r.close)}</div>
                </>
              )}
            </div>
          </div>
        ))}
        {rows.length === 0 && (
          <div className="py-5 text-center text-xs text-slate-400">暂无数据</div>
        )}
      </div>
    </div>
  )
}
