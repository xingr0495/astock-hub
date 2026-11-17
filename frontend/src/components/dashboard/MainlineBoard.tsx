import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { sessionRefreshMs } from '../../lib/refresh'
import { classifyLatest, fadingMembers, localDateStr, MAINLINE_BADGES, type MainRow } from '../../lib/mainline'

/**
 * 主线格局面板（B·复盘模式主线五分类的落地）：
 * 用 /api/mainline 的近 7 日榜单做确定性估算——
 *   单日脉冲：仅当日上榜  |  主线：rank1-2  |  次主线：rank3-4  |  轮动：rank5+
 *   退潮：前一日 top5 但当日缺席
 * 口径标注"估算"：这是方法论五分类的机械近似，人工复盘仍以 AI 判定为准。
 * 五分类/presence/本地日期串见 lib/mainline.ts（与板块分析页共用）。
 */

export function MainlineBoard({ onShowMembers, asOf }: {
  onShowMembers?: (name: string) => void
  asOf?: string
}) {
  // 每分钟重渲染一次，让分时段刷新节奏随时间推移生效（9:15/15:05 边界切换）
  const [, setTick] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), 60_000)
    return () => clearInterval(t)
  }, [])

  const q = useQuery({
    queryKey: ['mainline-board', asOf],
    queryFn: () => api.mainline(asOf ? 30 : 7, false, !asOf),
    staleTime: 60_000,
    refetchInterval: asOf ? false : sessionRefreshMs(),
  })
  const payload = q.data?.data as any
  const kinds = payload?.kinds
  const rt = payload?.realtime as { today?: string; rt_time?: string; intraday?: boolean } | undefined
  // 回看模式：截断到所选日为止——分类/presence 与"当天看"完全同口径
  const allRows: MainRow[] = (kinds?.concept ?? []) as MainRow[]
  const rows: MainRow[] = asOf ? allRows.filter((r) => r.date <= asOf) : allRows

  if (q.isLoading) {
    return (
      <div className="rounded-xl border border-surface-700 bg-surface-850 p-3 text-[11px] text-slate-500">
        主线格局加载中…
      </div>
    )
  }
  if (!rows.length) return null

  const dates = [...new Set(rows.map((r) => r.date))].sort()
  const latestDate = dates[dates.length - 1]
  const todayStr = localDateStr()
  const isLive = !asOf && rt?.intraday && latestDate === todayStr
  const viewingPast = !!asOf && latestDate !== todayStr
  const latest = rows.filter((r) => r.date === latestDate).sort((a, b) => a.rank - b.rank)

  // 近 5 日出现天数（presence）——圆点窗口与 title 提示共用
  const recentDates = dates.slice(-5)

  const classified = classifyLatest(latest.slice(0, 7), rows)
  const fading = fadingMembers(rows, 2)

  return (
    <div className="rounded-xl border border-surface-700 bg-surface-850 p-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-[11px] text-slate-400">主线格局 · 五分类（估算）</span>
        <span className="text-[9px] text-slate-600" title="机械近似：rank1-2主线/3-4次主线/5+轮动/仅当日=单日脉冲/前日top5缺席=退潮；人工复盘以 AI 判定为准">
          ⓘ 近7日榜单推算
        </span>
        <span className="ml-auto text-[9px] text-slate-600">
          {isLive ? `盘中实时 ${rt?.rt_time?.slice(11, 16) ?? ''}`
            : viewingPast ? `回看 ${latestDate.slice(5)}`
            : latestDate.slice(5)}
        </span>
      </div>

      <div className="space-y-1">
        {classified.map((r) => {
          const cls = MAINLINE_BADGES[r.cls]
          return (
            <div
              key={r.member}
              onClick={() => onShowMembers?.(r.member)}
              className="grid cursor-pointer grid-cols-[56px_1fr_auto_auto_46px] items-center gap-1.5 rounded-md bg-surface-800/60 px-2 py-1.5 hover:border hover:border-brand/40"
            >
              <span className={`rounded border px-1 py-px text-[9px] ${cls.cls}`}>{cls.text}</span>
              <span className="truncate text-[11px] text-slate-200">{r.member}</span>
              <span className="text-[10px] text-slate-400">涨停 {r.limit_up_count}</span>
              <span className="text-[10px] text-slate-400">{r.max_boards}板</span>
              <span className="flex justify-end gap-0.5" title={`近5日上榜 ${r.presence} 天`}>
                {recentDates.map((d) => (
                  <span
                    key={d}
                    className={`inline-block h-1.5 w-1.5 rounded-full ${
                      rows.some((x) => x.date === d && x.member === r.member)
                        ? 'bg-brand'
                        : 'bg-surface-600'
                    }`}
                  />
                ))}
              </span>
            </div>
          )
        })}

        {fading.length > 0 && (
          <>
            <div className="mt-1.5 text-[9px] text-slate-600">退潮（前日热门今日缺席）</div>
            {fading.map((r) => (
              <div
                key={r.member}
                onClick={() => onShowMembers?.(r.member)}
                className="grid cursor-pointer grid-cols-[56px_1fr_auto] items-center gap-1.5 rounded-md bg-surface-800/40 px-2 py-1 opacity-70 hover:border hover:border-brand/40"
              >
                <span className={`rounded border px-1 py-px text-[9px] ${MAINLINE_BADGES['退潮'].cls}`}>退潮</span>
                <span className="truncate text-[11px] text-slate-400">{r.member}</span>
                <span className="text-[10px] text-slate-600">曾 {r.max_boards}板</span>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  )
}
