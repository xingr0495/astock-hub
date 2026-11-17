import type { DashBreadth, DistributionRow } from "./types"

/** 涨跌幅 8 档分布柱状。 */
export function DistributionBars({ rows }: { rows: DistributionRow[] }) {
  const maxCount = Math.max(...rows.map((r) => r.count), 1)
  return (
    <div className="grid h-24 grid-cols-8 items-end gap-1 pt-1">
      {rows.map((r, i) => {
        const positive = i >= 4
        return (
          <div key={r.label} className="flex h-full min-w-0 flex-col items-center justify-end gap-0.5">
            <div className="font-mono text-[9px] text-slate-400">{r.count || ""}</div>
            <div
              className={`w-2 rounded-full ${positive ? "bg-gradient-to-t from-rose-500/45 to-rose-500/90" : "bg-gradient-to-t from-emerald-500/45 to-emerald-500/90"}`}
              style={{ height: `${Math.max(4, (r.count / maxCount) * 86)}%` }}
              title={`${r.label}: ${r.count}只`}
            />
            <div className="truncate text-[9px] text-slate-400">{r.label}</div>
          </div>
        )
      })}
    </div>
  )
}

/** 涨跌平广度条。 */
export function BreadthBar({ data }: { data: DashBreadth }) {
  const denom = Math.max(data.total, 1)
  const upW = (data.up / denom) * 100
  const downW = (data.down / denom) * 100
  const flatW = Math.max(0, 100 - upW - downW)
  return (
    <div className="space-y-2">
      <div className="flex h-2.5 overflow-hidden rounded-full bg-surface-800">
        <div className="bg-rose-500/85" style={{ width: `${upW}%` }} />
        <div className="bg-slate-500/45" style={{ width: `${flatW}%` }} />
        <div className="bg-emerald-500/85" style={{ width: `${downW}%` }} />
      </div>
      <div className="grid grid-cols-3 gap-1.5 text-[11px]">
        <div className="rounded bg-rose-500/10 px-2 py-1 text-rose-400">
          涨 <span className="font-mono">{data.up}</span>
        </div>
        <div className="rounded bg-surface-800 px-2 py-1 text-slate-400">
          平 <span className="font-mono">{data.flat}</span>
        </div>
        <div className="rounded bg-emerald-500/10 px-2 py-1 text-emerald-400">
          跌 <span className="font-mono">{data.down}</span>
        </div>
      </div>
    </div>
  )
}
