import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { FileText } from 'lucide-react'
import { api } from '../../lib/api'
import { useAuth } from '../../auth'

/**
 * 复盘状态条（C）：把刚建好的 reviews 落盘链路变成看板可见的入口——
 * 显示最新复盘日期/情绪/推送状态，点击跳大盘复盘页。
 */
export function ReviewStatusBar() {
  const navigate = useNavigate()
  const { isAdmin, can } = useAuth()
  // 权限闸（终审修复 2）：view_review 无权限不发挂载即查的请求（免 403×3 重试 +
  // 每 5 分钟再打），也不渲染"还没有大盘复盘+去生成"误导态；admin 恒真
  const canReview = isAdmin || can('view_review')
  const q = useQuery({
    queryKey: ['review-daily-status'],
    queryFn: () => api.reviewDailyStatus(),
    enabled: canReview,
    staleTime: 60_000,
    refetchInterval: 5 * 60_000,
  })
  const latest = (q.data as any)?.latest as
    | { as_of: string; meta: string; pushed: boolean }
    | null
  if (!canReview) return null
  if (q.isLoading) return null
  if (!latest) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-surface-700 bg-surface-850 px-3 py-1.5 text-[11px] text-slate-500">
        <FileText className="h-3.5 w-3.5" />
        还没有大盘复盘
        <button
          onClick={() => navigate('/review')}
          className="ml-auto rounded border border-surface-600 px-2 py-0.5 text-slate-300 hover:border-brand/50 hover:text-brand"
        >
          去生成
        </button>
      </div>
    )
  }
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-surface-700 bg-surface-850 px-3 py-1.5 text-[11px]">
      <FileText className="h-3.5 w-3.5 shrink-0 text-brand" />
      <span className="text-slate-300">最新复盘 <span className="font-medium text-slate-100">{latest.as_of}</span></span>
      {latest.meta && <span className="truncate text-slate-500">{latest.meta}</span>}
      <span className={latest.pushed ? 'text-emerald-400' : 'text-amber-400'}>
        {latest.pushed ? '✓ 已推送飞书' : '○ 未推送'}
      </span>
      <button
        onClick={() => navigate('/review')}
        className="ml-auto rounded border border-surface-600 px-2 py-0.5 text-slate-300 hover:border-brand/50 hover:text-brand"
      >
        查看 / 生成今日
      </button>
    </div>
  )
}
