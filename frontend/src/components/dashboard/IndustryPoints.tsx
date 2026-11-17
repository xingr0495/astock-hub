import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"

import { api } from "../../lib/api"
import { useAuth } from "../../auth"
import { cleanBlogger } from "../../lib/text"
import type { Envelope } from "../../lib/types"
import NewsDetailDialog, { type NewsItem } from "../NewsDetailDialog"

/** 今日产业要点：全球产业新闻 top_items 摘要 + 详情弹窗。 */
export function IndustryPoints() {
  const [selected, setSelected] = useState<NewsItem | null>(null)
  const { isAdmin, can } = useAuth()
  // 权限闸（终审修复 2）：view_news 无权限不发挂载即查的请求（免 403），
  // 整卡不渲染（否则常驻"暂无产业新闻要点"误导）；admin 恒真
  const canNews = isAdmin || can("view_news")
  const q = useQuery<Envelope>({
    queryKey: ["dash-news"],
    queryFn: () => api.news(),
    enabled: canNews,
    staleTime: 2 * 60_000,
  })

  const newsData = (q.data?.data as any)?.data as any
  const titles = useMemo(() => {
    const inds: any[] = newsData?.industries ?? []
    return inds.length && inds[0]?.top_items
      ? (inds[0].top_items as NewsItem[]).slice(0, 6)
      : inds
          .flatMap((i: any) => i.points ?? [])
          .map((p: any) => ({ title: p.t ?? "", source: "", time: "", zh: "" }))
          .slice(0, 6)
  }, [newsData])
  const label = newsData?.industries?.[0]?.name ?? "全球产业新闻"

  if (!canNews) return null

  return (
    <section className="rounded-xl border border-surface-700 bg-surface-850 p-4">
      <header className="mb-2 flex items-center gap-2">
        <h2 className="text-sm font-semibold text-slate-100">今日产业要点</h2>
        <span className="ml-auto text-[10px] text-slate-500">{label}</span>
      </header>
      {titles.length === 0 ? (
        <p className="text-xs text-slate-500">暂无产业新闻要点</p>
      ) : (
        <ul className="space-y-1.5">
          {titles.map((n, i) => (
            <li key={i} className="text-[12px] text-slate-300">
              <button
                className="flex w-full items-center gap-1.5 rounded px-0.5 py-0.5 text-left hover:bg-surface-700/50"
                onClick={() => setSelected(n)}
                title="查看详情"
              >
                <span className="shrink-0 text-[10px] text-slate-500">{n.time ?? ""}</span>
                <span className="truncate hover:text-brand">{cleanBlogger(n.zh || n.title)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {selected && <NewsDetailDialog item={selected} onClose={() => setSelected(null)} />}
    </section>
  )
}
