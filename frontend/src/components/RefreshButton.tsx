import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, QUICK_SOURCES, SOURCE_LABELS } from '../lib/api'
import { usePolling } from '../lib/queryKeys'
import { useAuth } from '../auth'

/** 一键刷新数据：启动后台刷新任务并轮询进度。仅管理员可见（/api/refresh 为 admin 级端点）。 */
export default function RefreshButton() {
  const { isAdmin } = useAuth()
  const [kick, setKick] = useState(0)
  const [busy, setBusy] = useState(false)

  const status = useQuery({
    queryKey: ['refreshStatus', kick],
    queryFn: api.refreshStatus,
    enabled: isAdmin,
    refetchInterval: (q) => (q.state.data?.running ? 4000 : false),
  })

  usePolling(!status.data?.running && busy, () => {
    setBusy(false)
  })

  // 无权限不渲染、不发请求（enabled 已拦请求，此处拦渲染）
  if (!isAdmin) return null

  const running = status.data?.running ?? false

  return (
    <div className="flex flex-wrap items-center gap-2">
      <button
        disabled={running || busy}
        onClick={async () => {
          setBusy(true)
          try {
            await api.refresh([...QUICK_SOURCES])
            setKick((k) => k + 1)
          } catch (e) {
            setBusy(false)
            alert(e instanceof Error ? e.message : String(e))
          }
        }}
        className="rounded-lg border border-brand/40 bg-brand/10 px-3 py-1.5 text-xs text-brand transition-colors hover:bg-brand/20 disabled:opacity-50"
      >
        {running ? <span className="inline-flex items-center gap-1">⟳ 刷新中…({(status.data?.sources ?? []).map((s) => SOURCE_LABELS[s] ?? s).join('/')})</span> : '⟳ 刷新数据'}
      </button>
      {status.data?.finished_at && (
        <span className="text-[11px] text-slate-500">上次刷新：{status.data.finished_at}</span>
      )}
    </div>
  )
}