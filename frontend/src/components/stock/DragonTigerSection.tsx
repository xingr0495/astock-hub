/**
 * 龙虎榜组件 - 显示个股龙虎榜数据
 */

import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { Flame } from 'lucide-react'

export function DragonTigerSection({ code, name }: { code: string; name?: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['dragon-tiger-stock', code],
    queryFn: () => api.dragonTigerStock(code),
    staleTime: 300_000, // 5分钟
    enabled: !!code,
  })

  if (isLoading) {
    return <div className="py-4 text-center text-xs text-slate-400">加载龙虎榜数据...</div>
  }

  if (error || !data?.ok) {
    return (
      <div className="py-4 text-center text-xs text-slate-500">
        {data?.error || '暂无龙虎榜数据'}
      </div>
    )
  }

  const items = data.data?.items || []
  if (items.length === 0) {
    return (
      <div className="py-4 text-center text-xs text-slate-500">
        近期未上榜
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-surface-700 bg-surface-850 p-4">
      <h3 className="mb-3 flex items-center gap-2 text-sm font-medium text-slate-200">
        <Flame className="h-4 w-4 text-orange-400" />
        龙虎榜记录（近{items.length}次）
      </h3>
      <div className="space-y-2 max-h-[300px] overflow-y-auto">
        {items.map((item: any, i: number) => (
          <div key={i} className="rounded-lg bg-surface-800 px-3 py-2">
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-200">{item.date}</span>
              <span className={`rounded px-2 py-0.5 text-xs ${
                (item.net_buy || 0) >= 0 ? 'bg-rose-500/20 text-rose-300' : 'bg-emerald-500/20 text-emerald-300'
              }`}>
                {item.net_buy >= 0 ? '净买' : '净卖'} {Math.abs(item.net_buy || 0).toFixed(2)}亿
              </span>
            </div>
            {item.reason && (
              <div className="mt-1 text-[11px] text-slate-500">
                原因: {item.reason}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
