import { useState } from "react"
import { useQuery } from "@tanstack/react-query"

import { api } from "../../lib/api"
import { useAuth } from "../../auth"
import { cleanBlogger, cleanReport } from "../../lib/text"
import { Modal } from "../Modal"
import type { Envelope } from "../../lib/types"

/** 最新星球调研纪要摘录；点击卡片弹窗查看全文汇总（弹窗与卡片并列，避免冒泡重开）。 */
export function LatestZsxq() {
  const [open, setOpen] = useState(false)
  const { isAdmin, can } = useAuth()
  // 权限闸（任务 11）：无 zsxq 权限不发挂载即查的请求，整体不渲染；admin 恒真
  const canZsxq = isAdmin || can("zsxq")
  const q = useQuery<Envelope>({
    queryKey: ["dash-zsxq-latest"],
    queryFn: () => api.zsxqLatest(),
    enabled: canZsxq,
    staleTime: 2 * 60_000,
  })
  const z = (q.data?.data as any)?.data as any

  const reportQ = useQuery({
    queryKey: ["dash-zsxq-report", z?.date],
    queryFn: () => api.zsxqReport(z.date as string),
    enabled: open && !!z?.date,
    staleTime: 60_000,
  })

  if (!canZsxq) return null

  return (
    <>
      <section
        onClick={() => z?.date && setOpen(true)}
        className={`rounded-xl border border-surface-700 bg-surface-850 p-4 transition-colors ${
          z?.date ? "cursor-pointer hover:border-brand/40" : ""
        }`}
        title={z?.date ? "点击查看全文汇总" : undefined}
      >
        <header className="mb-2 flex items-center gap-2">
          <h2 className="text-sm font-semibold text-slate-100">最新星球纪要</h2>
          <span className="ml-auto text-[10px] text-slate-500">{z?.date ?? ""}</span>
          {z?.date && (
            <span className="text-[10px] text-brand">查看全文 ›</span>
          )}
        </header>
        <p className="line-clamp-6 whitespace-pre-line text-[12px] leading-relaxed text-slate-400">
          {cleanBlogger(z?.excerpt).slice(0, 520) ?? "暂无纪要数据"}
        </p>
      </section>

      {open && z?.date && (
        <Modal onClose={() => setOpen(false)} labelledBy="dash-zsxq-title"
          panelClassName="w-[92vw] max-w-2xl rounded-xl border border-surface-700 bg-surface-850 shadow-2xl">
          <header className="flex items-center justify-between border-b border-surface-700 p-3">
            <h2 id="dash-zsxq-title" className="text-sm font-semibold text-slate-100">
              星球纪要汇总
              <span className="ml-2 text-xs font-normal text-slate-400">
                {z.date}
                {reportQ.data?.chars ? ` · ${reportQ.data.chars} 字` : ""}
              </span>
            </h2>
            <button
              onClick={() => setOpen(false)}
              className="rounded px-1.5 text-slate-400 hover:bg-surface-700 hover:text-slate-200"
            >
              ✕
            </button>
          </header>
          <div className="max-h-[70vh] overflow-y-auto p-4">
            {reportQ.isLoading && (
              <p className="text-xs text-slate-500">加载全文…</p>
            )}
            {reportQ.data && !reportQ.data.ok && (
              <p className="text-xs text-rose-300">{reportQ.data.error}</p>
            )}
            {reportQ.data?.ok && (
              <div className="whitespace-pre-wrap text-[12px] leading-relaxed text-slate-300">
                {cleanReport(reportQ.data.content)}
              </div>
            )}
          </div>
        </Modal>
      )}
    </>
  )
}
