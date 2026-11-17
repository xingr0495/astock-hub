import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import NewsDetailDialog, { type NewsItem } from '../components/NewsDetailDialog'
import { useAuth } from '../auth'

interface Industry {
  key: string
  name: string
  total: number
  top_items: NewsItem[]
}

interface RefreshStatus {
  running: boolean
  sources: string[]
  started_at?: string | null
  finished_at?: string | null
}

/**
 * 产业资讯：investment-news 12 大赛道的原生页面（非 iframe，单进程单端口）。
 * 左侧选赛道 → 右侧新闻列表 → 点条目弹详情。
 */

/** 从刷新结果里提取一句给用户看的状态提示；取不到返回 null。 */
function _refreshNotice(result: any): string | null {
  const news = result?.dimensions?.news
  if (!news)
    return null
  if (news.status === 'ok')
    return news.data?.note ?? '资讯已更新'
  if (news.status === 'skipped')
    return news.data?.note ?? '已是最新'
  return news.error?.hint ?? '资讯刷新未完成'
}

export default function NewsPage() {
  const qc = useQueryClient()
  // 免费版：纪要/本地资料已剔除，只保留资讯赛道与管理员刷新
  const { isAdmin } = useAuth()
  const [activeKey, setActiveKey] = useState<string | null>(null)
  const [selected, setSelected] = useState<NewsItem | null>(null)

  const [refreshing, setRefreshing] = useState(false)
  const [refreshErr, setRefreshErr] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const query = useQuery({
    queryKey: ['news-full'],
    queryFn: () => api.news(),
    staleTime: 60_000,
  })

  const industries = useMemo<Industry[]>(() => {
    const data = (query.data?.data as any)?.data
    return (data?.industries ?? []) as Industry[]
  }, [query.data])

  const active = useMemo(
    () => industries.find((i) => i.key === activeKey) ?? industries[0] ?? null,
    [industries, activeKey],
  )

  useEffect(() => () => stopPolling(), [])

  function stopPolling() {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  async function refreshNews() {
    if (refreshing)
      return
    setRefreshErr(null)
    setNotice(null)
    setRefreshing(true)
    try {
      await api.refresh(['news'])
    } catch (e) {
      setRefreshing(false)
      setRefreshErr(`启动刷新失败：${(e as Error).message}`)
      return
    }
    stopPolling()
    pollRef.current = setInterval(async () => {
      try {
        const s = await api.refreshStatus()
        if (!s.running) {
          stopPolling()
          setRefreshing(false)
          qc.invalidateQueries({ queryKey: ['news-full'] })
          setNotice(_refreshNotice(s.result))
        }
      } catch {
        /* 单次轮询失败，下一秒重试 */
      }
    }, 1000)
  }

  return (
    <div className="rounded-xl border border-surface-700">
      <div className="flex flex-col md:flex-row md:h-[70vh] md:min-h-[480px]">
        {/* 赛道侧栏：手机=顶部横向滑动胶囊条；桌面=左栏 */}
        <aside className="flex w-full shrink-0 flex-row gap-1 overflow-x-auto border-b border-surface-700 bg-surface-850 p-2 md:w-52 md:flex-col md:gap-0 md:overflow-x-visible md:overflow-y-auto md:border-b-0 md:border-r">
          <div className="hidden px-2 py-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500 md:block">
            产业赛道
          </div>
          {query.isLoading && (
            <div className="hidden px-2 py-4 text-xs text-slate-500 md:block">加载中…</div>
          )}
          {industries.map((ind) => (
            <button
              key={ind.key}
              onClick={() => setActiveKey(ind.key)}
              className={`flex w-auto shrink-0 whitespace-nowrap items-center justify-between rounded-md px-2.5 py-2 text-left text-[12px] transition-colors md:w-full ${
                active?.key === ind.key
                  ? 'bg-brand/15 text-brand'
                  : 'text-slate-300 hover:bg-surface-700/60'
              }`}
            >
              <span className="truncate">{ind.name}</span>
              <span className="ml-2 shrink-0 text-[10px] text-slate-500">{ind.total}</span>
            </button>
          ))}
        </aside>

        {/* 新闻列表（免费版：纪要/本地资料面板已剔除，只保留资讯列表） */}
        <section className="min-w-0 flex-1 overflow-y-auto p-3 md:p-5">
          {active && (
            <>
              <header className="mb-3 flex items-center gap-2">
                <h1 className="text-base font-semibold text-slate-100">{active.name}</h1>
                <span className="text-[11px] text-slate-500">· {active.total} 条</span>
                {/* 刷新资讯=启动数据链（/api/refresh admin 级端点）：仅管理员可见，非管理员点按会 403 */}
                {isAdmin && (
                  <button
                    type="button"
                    onClick={refreshNews}
                    disabled={refreshing}
                    title="重新抓取并提炼最新资讯"
                    className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-surface-600 px-2.5 py-1 text-[11px] text-slate-300 transition-colors hover:border-brand/50 hover:text-brand disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <span className={refreshing ? 'inline-block animate-spin' : ''}>🔄</span>
                    {refreshing ? '刷新中…' : '刷新资讯'}
                  </button>
                )}
              </header>

              {refreshErr && (
                <p className="mb-2 text-[11px] text-rose-300">{refreshErr}</p>
              )}

              {notice && (
                <div className="mb-2 flex items-center gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-1.5 text-[11px] text-emerald-300">
                  <span className="flex-1">{notice}</span>
                  <button
                    type="button"
                    onClick={() => setNotice(null)}
                    className="shrink-0 text-emerald-300/70 hover:text-emerald-200"
                    aria-label="关闭提示"
                  >
                    ✕
                  </button>
                </div>
              )}

              {active.top_items.length === 0 ? (
                <p className="text-xs text-slate-500">该赛道暂无条目</p>
              ) : (
                <ul className="space-y-1.5">
                  {active.top_items.map((n, i) => (
                    <li key={i}>
                      <button
                        className="flex w-full items-center gap-2 rounded-lg border border-surface-700/60 bg-surface-850 px-3 py-2 text-left hover:border-brand/40"
                        onClick={() => setSelected(n)}
                      >
                        <span className="shrink-0 text-[10px] text-slate-500">{n.time ?? ''}</span>
                        <span className="min-w-0 flex-1 truncate text-[12px] text-slate-200">
                          {n.zh || n.title}
                        </span>
                        {n.source && (
                          <span className="shrink-0 text-[10px] text-slate-500">{n.source}</span>
                        )}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </section>
      </div>

      {selected && <NewsDetailDialog item={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}
