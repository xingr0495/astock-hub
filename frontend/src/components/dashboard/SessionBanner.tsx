import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Sunrise, Coffee } from 'lucide-react'
import { api } from '../../lib/api'
import { useAuth } from '../../auth'
import NewsDetailDialog, { type NewsItem } from '../NewsDetailDialog'

/**
 * 会话横幅（D2·三态化）：
 * - 盘前（工作日 <9:15 或 <9:30）：说明当前显示的是哪天收盘 + 隔夜要闻（前 12 条，
 *   区域内滚动查看，点击任意一条弹窗看详情，2026-10-21 用户要求）
 * - 非交易日：说明当前为最近交易日收盘数据
 * - 盘中/盘后不渲染（顶栏的"盘中实时/盘后"与复盘状态条已覆盖）
 */
export function SessionBanner({ asOf, marketSession, dbMaxDate, tradingDay }: {
  asOf?: string
  marketSession?: string
  dbMaxDate?: string
  tradingDay?: boolean | null
}) {
  const [selected, setSelected] = useState<NewsItem | null>(null)
  const { isAdmin, can } = useAuth()
  // 权限闸（终审修复 2）：view_news 无权限不发挂载即查的请求（免 403）。
  // 只关要闻腿、不整卡 return null——盘前/休市横幅本身与新闻无关，访客仍要看到；
  // 查询关闭时 headlines 为空，要闻列表自然不渲染（admin 恒真零变化）
  const canNews = isAdmin || can('view_news')
  const newsQuery = useQuery({
    queryKey: ['session-news'],
    queryFn: () => api.news(),
    enabled: canNews,
    staleTime: 10 * 60_000,
    refetchInterval: 10 * 60_000,
  })

  const now = new Date()
  const isWeekday = now.getDay() >= 1 && now.getDay() <= 5

  // 节假日判定（2026-10-19 方案B）：后端 trading_day（开盘红法定节假日表）
  // 为准；后端未给/None（kph 故障）回退旧启发式。
  // 旧启发式问题：任何交易日 16:25 链落地前本地必落后 1 个工作日，
  // missedWeekdays≥1 会把整个白天误标"非交易日"（10-16 周五误判反馈）。
  const missedWeekdays = (() => {
    if (!dbMaxDate) return 0
    const d = new Date(`${dbMaxDate}T00:00:00`)
    let missed = 0
    while (d < now) {
      d.setDate(d.getDate() + 1)
      if (d.getDay() >= 1 && d.getDay() <= 5 && d < now) missed++
    }
    return missed
  })()
  const holiday = tradingDay != null
    ? tradingDay === false
    : isWeekday && missedWeekdays >= 1
  const preMarket = isWeekday && !holiday && marketSession === 'pre'
  const nonTradingDay = !isWeekday || holiday

  if (!preMarket && !nonTradingDay) return null

  // 隔夜要闻：优先取"财经/宏观"赛道，否则首赛道前 3 条
  const industries = ((newsQuery.data as any)?.data?.data?.industries ?? []) as
    { key: string; name: string; top_items?: any[] }[]
  const pick =
    industries.find((i) => i.name.includes('财经') || i.name.includes('宏观')) ?? industries[0]
  const headlines = (pick?.top_items ?? []).slice(0, 12)

  return (
    <div className="rounded-xl border border-surface-700 bg-surface-850 px-3 py-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
        <span className="inline-flex items-center gap-1 font-medium text-amber-300">
          {preMarket ? <Sunrise className="h-3.5 w-3.5" /> : <Coffee className="h-3.5 w-3.5" />}
          {preMarket ? `盘前 · 09:30 开盘` : '非交易日 / 休市'}
        </span>
        <span className="text-slate-500">
          当前展示 <span className="text-slate-300">{asOf ?? '最近交易日'}</span> 收盘数据
        </span>
        {preMarket && (
          <span className="text-slate-600">盘中将自动切换实时轮询（20 秒）</span>
        )}
        <span className="ml-auto text-slate-500">提示：盘前看隔夜要闻，开盘后看主线格局</span>
      </div>
      {preMarket && headlines.length > 0 && (
        <div className="mt-1.5 flex max-h-[88px] flex-col gap-0.5 overflow-y-auto pr-1 [scrollbar-width:thin]">
          {headlines.map((h, i) => (
            <div
              key={i}
              onClick={() => setSelected(h)}
              title="点击查看详情"
              className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 text-[11px] transition-colors hover:bg-surface-800/60"
            >
              <span className="shrink-0 text-slate-600">•</span>
              <span className="truncate text-slate-400" title={h.zh || h.title}>
                {h.zh || h.title}
              </span>
              {h.time && <span className="shrink-0 text-[9px] text-slate-600">{String(h.time).slice(5, 16)}</span>}
            </div>
          ))}
        </div>
      )}
      {selected && (
        <NewsDetailDialog item={selected} onClose={() => setSelected(null)} />
      )}
      {nonTradingDay && (
        <div className="mt-1 text-[10px] text-slate-600">
          休市期间看板为静态数据，下一交易日 09:30 起自动转实时轮询。
        </div>
      )}
    </div>
  )
}
