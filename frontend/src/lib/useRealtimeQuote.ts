import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'

export interface RealtimeTick {
  price: number
  changePct: number
  open: number
  high: number
  low: number
  volume: number
}

/**
 * 同花顺同源实时行情。盘中（9:30-11:30 / 13:00-15:00）每 5 秒自动轮询，
 * 盘后不请求（返回 null，调用方沿用 bundle 的静态收盘价）。
 *
 * 传入带后缀的 ts_code（如 002837.SZ）；为空时不启用。
 */
export function useRealtimeQuote(tsCode: string | undefined) {
  const query = useQuery({
    queryKey: ['realtime-quote', tsCode],
    enabled: !!tsCode,
    queryFn: () => api.quote(tsCode!),
    refetchInterval: (q) => {
      const data = q.state.data
      const trading = (data?.data as any)?.is_trading
      return trading ? 5000 : false
    },
    staleTime: 3000,
  })

  const payload = (query.data?.data as any) ?? {}
  const item = payload.item?.[0]
  const isTrading = payload.is_trading === true

  if (!item || item.last_price == null) {
    return { tick: null, isTrading }
  }
  const tick: RealtimeTick = {
    price: Number(item.last_price),
    // market snapshot 的 price_change_ratio_pct 已是百分比数值（0.26 = +0.26%）
    changePct: Number(item.price_change_ratio_pct ?? 0),
    open: Number(item.open_price),
    high: Number(item.high_price),
    low: Number(item.low_price),
    volume: Number(item.volume),
  }
  return { tick, isTrading }
}
