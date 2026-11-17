/** 实时看板数据类型（与后端 market/dashboard.py 返回对齐）。 */

export interface DashIndex {
  symbol: string
  name: string
  last_price: number | null
  change_pct: number | null // 指数：百分数
  change_amount: number | null
}

export interface DashBreadth {
  total: number
  up: number
  down: number
  flat: number
  up_pct: number
  down_pct: number
  avg_pct: number | null // 个股：小数
  median_pct: number | null
  strong_up: number
  strong_down: number
}

export interface TierStock {
  symbol: string
  name: string | null
  amount: number
}

export interface DashLimit {
  limit_up: number
  broken: number
  failed: number
  limit_down: number
  max_boards: number
  seal_rate: number
  tiers: { boards: number; count: number; stocks: TierStock[] }[]
  sealed_ready: boolean
  fake_up: number
  fake_down: number
}

export interface DashTrend {
  above_ma5: number
  above_ma20: number
  above_ma60: number
  above_ma5_pct: number
  above_ma20_pct: number
  above_ma60_pct: number
  new_high: number
  new_low: number
}

export interface DashActivity {
  avg_turnover: number | null
  high_turnover: number
  high_vol_ratio: number
  vol_ratio: number | null
}

export interface DashRadar {
  key: string
  label: string
  value: number
}

export interface DashRankItem {
  name: string
  count: number
  avg_pct: number // 小数
  up_count: number
  down_count: number
  amount: number
  source_field: string
  leader: {
    symbol: string | null
    name: string | null
    change_pct: number | null
  }
}

export interface DashRank {
  leading: DashRankItem[]
  lagging: DashRankItem[]
}

export interface DashRow {
  symbol: string
  name: string | null
  close: number
  change_pct: number // 小数
  amount: number
  turnover_rate: number | null
  board: string
}

export interface DistributionRow {
  label: string
  count: number
  pct: number
}

export interface DashboardData {
  as_of: string
  indices: DashIndex[]
  breadth: DashBreadth
  amount: { total: number; avg: number }
  boards: unknown[]
  limit: DashLimit
  distribution: DistributionRow[]
  trend: DashTrend
  activity: DashActivity
  radar: DashRadar[]
  emotion: { score: number; label: string }
  top_gainers: DashRow[]
  top_losers: DashRow[]
  turnover_leaders: DashRow[]
  active_leaders: DashRow[]
  concept_rank: DashRank
  industry_rank: DashRank
  meta: {
    mode: string
    market_session: string
    is_trading: boolean
    db_max_date: string
    server_time: string
    turnover_available: boolean
  }
}
