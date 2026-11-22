/** 与 astock_hub/models.py 的 Envelope / DimensionResult 对齐 */

export interface SourceMeta {
  as_of: string | null
  fetched_at: string
  freshness_kind?: string
  freshness?: {
    ttl_days?: number
    trading_day_approx?: boolean
    age_days?: number
    stale?: boolean
    note?: string
  } | null
  endpoint_or_path?: string | null
  latency_ms?: number | null
  note?: string | null
}

export interface ErrorInfo {
  code: string
  message: string
  hint?: string | null
  retryable?: boolean
}

export interface DimensionResult {
  status: string
  source: string
  meta: SourceMeta
  data: unknown
  error: ErrorInfo | null
}

export interface Envelope {
  schema_version: string
  command: string
  generated_at: string
  query?: Record<string, unknown>
  dimensions?: Record<string, DimensionResult>
  summary?: Record<string, number>
  data?: unknown
}

export interface SearchItem {
  thscode: string
  ticker: string
  name: string
  exchange: string
  asset_type: string
}

/** TradingAgents 深度分析结果（ta_run.py 输出） */
export interface TaResult {
  code: string
  date: string
  decision: string
  final_trade_decision: string
  investment_plan: string
  trader_investment_plan: string
  reports: Record<string, string>
  debate_judge: string
  risk_judge: string
}

/** 各 Agent 报告的中文标题 */
export const TA_REPORT_LABELS: Record<string, string> = {
  market_report: '市场分析',
  sentiment_report: '情绪分析',
  news_report: '新闻分析',
  fundamentals_report: '基本面分析',
  policy_report: '政策分析',
  hot_money_report: '游资追踪',
  lockup_report: '解禁监控',
}

/** serenity 研究任务状态（GET /api/serenity/analyze） */
export interface SerenityJob {
  status: 'none' | 'running' | 'done' | 'failed'
  code?: string
  name?: string
  started_at?: string
  finished_at?: string
  result?: SerenityReport
  error?: string
  note?: string
}

/** serenity 产业链深度研究报告（serenity.py 输出） */
export interface SerenityReport {
  code: string
  name: string
  model: string
  key_source: string
  context: { price?: number; change_pct?: number; pe_ttm?: number; pb?: number }
  report: {
    one_line?: string
    scarce_layers?: { layer: string; reason: string; strength: string }[]
    company_position?: { layer: string; role: string; why: string }
    evidence?: { point: string; strength: string }[]
    market_missing?: string
    bear_case?: string
    risks?: string[]
    next_checks?: string[]
    raw?: string
    /** 模型输出被 max_tokens 截断时置 true（仅截断点之前的字段可用） */
    _partial?: boolean
  }
}

export interface StrategyParam {
  id: string
  label: string
  type: string
  default?: number | boolean
  min?: number
  max?: number
  step?: number
}

export interface StrategyMeta {
  id: string
  name: string
  description: string
  tags: string[]
  params: StrategyParam[]
  limit: number
}

export interface KlineRow {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number
  change_pct: number
  ma5?: number
  ma10?: number
  ma20?: number
  ma30?: number
  ma60?: number
  macd_dif?: number
  macd_dea?: number
  macd_hist?: number
  kdj_k?: number
  kdj_d?: number
  kdj_j?: number
  rsi?: number
  rsi_6?: number
  rsi_14?: number
  rsi_24?: number
  boll_upper?: number
  boll_mid?: number
  boll_lower?: number
  atr_14?: number
  vol_ratio_5d?: number
  high_60d?: number
  low_60d?: number
}

/** 面板展示的维度中文名与图标 */
export const DIMENSION_LABELS: Record<string, { title: string; icon: string }> = {
  market: { title: '行情技术面', icon: '📈' },
  serenity: { title: '产业链基本面', icon: '🔬' },
  national_team: { title: '国家队资金面', icon: '🏦' },
  timing: { title: '市场结构与择时', icon: '📊' },
  zsxq: { title: '信息面', icon: '📡' },
}