import { useQuery } from "@tanstack/react-query"

import { api } from "../../lib/api"

/** 收盘核对状态标签：通过=绿、参考源不可用已降级=琥珀、失败=红；未跑则不显示。 */
export function ReconcileBadge() {
  const q = useQuery({
    queryKey: ["dash-reconcile"],
    queryFn: () => api.dashboardReconcile(),
    staleTime: 30_000,
  })
  const d = q.data
  if (!d?.available) return null

  // passed=null + ref_unavailable = 参考源不可用的降级通过（不是核对失败）
  const degraded = !!d.ref_unavailable || d.passed == null
  const failed = Object.values(d.checks ?? {}).filter((c) => !c.ok)
  const title = d.passed
    ? `收盘核对通过（${d.as_of}）`
    : degraded
      ? `收盘核对已降级：参考源不可用，本地数据已过日期门（${d.as_of}）`
      : "收盘核对未通过" +
        `（${d.as_of}）` +
        (failed.length ? `\n不符项：${failed.map((_, i) => Object.keys(d.checks!).filter((k) => !d.checks![k].ok)[i]).join("、")}` : "")

  const cls = d.passed
    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
    : degraded
      ? "border-amber-500/40 bg-amber-500/10 text-amber-300"
      : "border-rose-500/40 bg-rose-500/10 text-rose-300"
  const label = d.passed ? "✓ 核对通过" : degraded ? "◐ 已降级" : "✗ 核对未过"

  return (
    <span title={title} className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${cls}`}>
      {label} · {d.as_of?.slice(5)}
    </span>
  )
}
