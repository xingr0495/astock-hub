// TQ 交易通道 API 封装（/api/tq）
export interface TqStatus {
  enabled: boolean
  kill_active: boolean
  account_ok: boolean
  account_error?: { message?: string; hint?: string }
}
export interface TqEnvelope<T = unknown> {
  status: 'ok' | 'unavailable' | 'error' | 'rejected' | 'duplicate' | 'pending_confirm' | 'submitted'
  data?: T
  error?: { message?: string; hint?: string }
  problems?: string[]
  msg?: string
  wtbh?: string
  /** 单已提交而本地账本没记上（磁盘满等）：不可静默，前端必须醒目提示核对委托表 */
  ledger_error?: string
}

// ---- 资产 / 持仓 / 委托行（TQ query_stock_* 返回，字段均为字符串） ----
export interface AssetRow {
  Cash?: string
  Asset?: string
  MarketValue?: string
  TotalFreeze?: string
  CurrentEquity?: string
}
export interface PosRow {
  Code?: string
  Name?: string
  Cbj?: string
  TotalVol?: string
  CanUseVol?: string
  /** 行情与派生增强（后端逐只快照；失败缺省） */
  Now?: number
  MktValue?: number
  Pnl?: number
  PnlPct?: number | null
  /** 当前成本 = TotalVol×Cbj；未可用 = 总持仓−可卖（在途+冻结合计，TQ 不分列） */
  CostAmt?: number
  Unavail?: number
}
export interface OrderRow {
  Code?: string
  Name?: string
  Wtbh?: string
  [k: string]: unknown
}
/** TQ 列表查询信封（实测 HTTP 口径）：data = { ErrorId, Value: 行数组 }。 */
export interface TqRows<T> {
  ErrorId?: string
  Value?: T[]
}
/** 历史委托行（面板账本：下单/拒单/撤单事件，按日归档）。 */
export interface HistoryRow {
  date?: string
  ts?: string
  side?: string
  code?: string
  price?: number
  amount?: number
  state?: string
  wtbh?: string
  msg?: string
  problems?: string[]
}

async function j<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init)
  return r.json() as Promise<T>
}
const post = <T = unknown>(url: string, body: unknown): Promise<T> =>
  j<T>(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export const tqApi = {
  status: () => j<TqStatus>('/api/tq/status'),
  asset: () => j<TqEnvelope<AssetRow>>('/api/tq/asset'),
  positions: () => j<TqEnvelope<TqRows<PosRow>>>('/api/tq/positions'),
  orders: () => j<TqEnvelope<TqRows<OrderRow>>>('/api/tq/orders'),
  orderHistory: (limit = 200) => j<{ status: string; data?: HistoryRow[] }>(`/api/tq/order-history?limit=${limit}`),
  quote: (code: string) =>
    j<{
      status: string
      data?: {
        code: string
        name: string
        now: number | null
        last_close: number | null
        pct: number | null
        bid: number | null
        ask: number | null
        bids?: (number | null)[]
        asks?: (number | null)[]
        bid_vols?: (number | null)[]
        ask_vols?: (number | null)[]
        weibi?: number | null
      }
      error?: { message?: string; hint?: string }
      problems?: string[]
    }>(`/api/tq/quote?code=${encodeURIComponent(code)}`),
  order: (o: { side: string; code: string; price: number; amount: number; intent_id: string }) =>
    post<TqEnvelope>('/api/tq/order', o),
  cancel: (code: string, wtbh: string) => post<TqEnvelope>('/api/tq/cancel', { code, wtbh }),
  kill: (enabled: boolean) =>
    post<TqEnvelope | { kill_active?: boolean }>('/api/tq/kill', { enabled }),
}
