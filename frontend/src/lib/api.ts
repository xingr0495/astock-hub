import type { Envelope, SearchItem } from './types'

// 前端统一走相对路径（Vite dev 代理 /api → 后端；生产由 FastAPI 同源托管）
const BASE = ''

// 因子库类型定义
export type FactorKind = 'base' | 'virtual' | 'composite' | 'custom'
export type FactorStability = 'stable' | 'experimental' | 'deprecated'

export interface FactorLibraryItem {
  id: string
  label: string
  group: string
  kind: FactorKind
  version: number
  formula: string
  direction: 'high' | 'low' | 'none'
  unit: string
  warmup_bars: number
  pit: boolean
  asset_types: string[]
  stability: FactorStability
  scale_free: boolean
  dependencies: string[]
}

export interface FactorDslError {
  code: string
  message: string
  position?: { offset: number; line: number }
}

export async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) {
    if (res.status === 401) {
      window.dispatchEvent(new CustomEvent('ah:401'))
    }
    let detail = ''
    try {
      detail = await res.text()
    } catch {
      /* ignore */
    }
    throw new Error(`请求失败 ${res.status} ${path} ${detail.slice(0, 200)}`)
  }
  return res.json() as Promise<T>
}

/** 通用 POST/DELETE JSON（凭证设置用） */
export async function sendJson<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const text = await res.text()
  const data = text ? JSON.parse(text) : {}
  if (!res.ok) {
    if (res.status === 401) {
      window.dispatchEvent(new CustomEvent('ah:401'))
    }
    throw new Error(data?.error ?? data?.message ?? `请求失败 ${res.status}`)
  }
  return data as T
}

export const postJson = <T,>(path: string, body: unknown) =>
  sendJson<T>('POST', path, body)
export const deleteJson = <T,>(path: string) =>
  sendJson<T>('DELETE', path)

// ==================== 星球纪要汇总 ====================

export interface ZsxqDayStatus {
  date: string
  weekday: string
  is_today: boolean
  has_topics: boolean
  topic_count: number | null
  has_report: boolean
  report_path: string | null
  state: 'ok' | 'raw_only' | 'missing'
}

export type ZsxqStepState = 'pending' | 'running' | 'ok' | 'skip' | 'fail'

export interface ZsxqDigestDay {
  date: string
  weekday: string
  fetch: ZsxqStepState
  summarize: ZsxqStepState
  note: string
}

export interface ZsxqDigestStatus {
  running: boolean
  started_at: string | null
  finished_at: string | null
  error: string | null
  days: ZsxqDigestDay[]
  progress: { done: number; total: number; summarized: number }
}

// ---- 本地资料（产业资讯页"本地资料"面板） ----

export interface LocalDoc {
  doc_id: string
  name: string
  ext: string
  upload_date: string
  chars: number
  status: 'summarizing' | 'ready' | 'summary_failed' | 'extract_failed'
  error?: string | null
  summary_preview?: string | null
}

/** 把字符串参数安全拼进查询串 */
export function qs(obj: Record<string, string | number | undefined>): string {
  const parts = Object.entries(obj)
    .filter(([, v]) => v !== undefined && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
  return parts.length ? `?${parts.join('&')}` : ''
}

export const api = {
  health: () => getJson<Envelope>('/api/health'),

  bundle: (code: string) =>
    getJson<Envelope>(`/api/bundle${qs({ code })}`),

  kline: (code: string, days = 120) =>
    getJson<Envelope>(`/api/kline${qs({ code, days })}`),

  // 分时（不传 source 走默认腾讯；data 形状见 IntradayPayload）
  intraday: (code: string) =>
    getJson<Envelope>(`/api/intraday${qs({ code })}`),

  valuation: (code: string) =>
    getJson<Envelope>(`/api/valuation${qs({ code })}`),

  quotes: (code: string) =>
    getJson<Envelope>(`/api/quotes${qs({ code })}`),

  // 大盘复盘
  reviewAnalyze: async (focus?: string, asOf?: string) => {
    try {
      const res = await fetch(`${BASE}/api/review/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ focus, as_of: asOf || undefined }),
      })
      const text = await res.text()
      try {
        return JSON.parse(text) as {
          ok: boolean
          as_of: string
          content?: string
          summary?: string
          emotion_score?: number
          emotion_label?: string
          error?: string
        }
      } catch {
        // 服务宕机/被代理截断时响应体为空或非 JSON，给可读错误而非原始异常
        return {
          ok: false,
          as_of: asOf ?? '',
          error: `面板服务无响应（HTTP ${res.status}）——服务可能已停止或正在重启，请稍后重试；若持续失败请运行 scripts\\start-web.ps1 启动服务`,
        }
      }
    } catch (e) {
      return {
        ok: false,
        as_of: asOf ?? '',
        error: `连不上面板服务（${(e as Error).message}）——请确认服务已启动（scripts\\start-web.ps1）`,
      }
    }
  },

  reviewReports: () =>
    getJson<{ reports: any[] }>('/api/review/reports'),

  reviewSave: (report: { id: string; as_of: string; content: string; summary: string; emotion_score?: number; emotion_label?: string }) =>
    fetch(`${BASE}/api/review/reports`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(report),
    }).then(r => r.json()),

  reviewDelete: (id: string) =>
    fetch(`${BASE}/api/review/reports/${id}`, { method: 'DELETE' }).then(r => r.json()),

  // 看板复盘状态条：reviews/market 最新 md + 推送标记
  reviewDailyStatus: () =>
    getJson<{ latest: { as_of: string; meta: string; pushed: boolean; path: string } | null }>(
      '/api/review/daily-status'),

  // 策略相关 API
  screenerStrategies: () =>
    getJson<{ presets: ScreenerStrategy[] }>('/api/strategies'),

  strategySaveComposite: (config: {
    strategy_id: string;
    name: string;
    description: string;
    children: { strategy_id: string; weight: number }[];
    merge_mode: string;
    min_confirm: number;
    mode: string;
  }) =>
    fetch(`${BASE}/api/strategy/composite`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    }).then(r => r.json()),

  strategyGet: (id: string) =>
    getJson<any>(`/api/strategy/${id}`),

  strategyAiStatus: () =>
    getJson<{ configured: boolean }>('/api/strategy/ai-status'),

  // 因子分析 API
  factors: (assetType = 'stock') =>
    getJson<{ factors: any[]; total: number }>(`/api/factors${qs({ asset_type: assetType })}`),

  factorValidate: (formula: string) =>
    fetch(`${BASE}/api/factors/validate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ formula }),
    }).then(r => r.json()),

  // 财务报表 API
  financial: (code: string) =>
    getJson<{ ok: boolean; data: any }>(`/api/financial/${code}`),

  // 个股龙虎榜 API
  dragonTigerStock: (code: string) =>
    getJson<{ ok: boolean; data?: any; error?: string }>(`/api/dragon-tiger/stock/${code}`),

  // 实时快照（同花顺同源）；返回 data.item + is_trading + market_session
  quote: (codes: string) =>
    getJson<Envelope>(`/api/quote${qs({ codes })}`),

  // 资金流向（日级历史：主力/超大单/大单/中单/小单净流入）
  fundFlow: (code: string, days = 30) =>
    getJson<Envelope>(`/api/fund-flow${qs({ code, days })}`),

  // 板块资金流向（concept/industry, today/5d/10d；trade_date 选过去交易日=单日历史）
  boardFundFlow: (boardType: string, period: string, limit = 20, tradeDate?: string) =>
    getJson<Envelope>(`/api/board-fund-flow${qs({ board_type: boardType, period, limit, trade_date: tradeDate })}`),

  indexes: () => getJson<Envelope>('/api/indexes'),

  // 实时看板（post/realtime；data 为 DashboardData）
  dashboard: (date?: string, realtime: 'auto' | 'true' | 'false' = 'auto', skipTurnover: boolean = false) =>
    getJson<Envelope>(`/api/dashboard${qs({ date, realtime, skip_turnover: skipTurnover })}`),

  dashboardReconcile: () =>
    getJson<{
      available: boolean
      as_of?: string
      passed?: boolean
      checks?: Record<string, { local: number; ifind: number; diff: number; ok: boolean }>
    }>('/api/dashboard/reconcile'),

  dashboardMembers: (kind: 'concept' | 'industry', name: string, realtime = false) =>
    getJson<{
      as_of: string
      intraday?: boolean
      rt_time?: string | null
      total: number
      rows: { symbol: string; name: string | null; change_pct: number | null }[]
    }>(`/api/dashboard/members${qs({ kind, name, realtime })}`),

  amountHistory: (days = 60) =>
    getJson<Envelope>(`/api/dashboard/amount-history${qs({ days })}`),

  // 美股/港股（TickFlow 在线行情，当前用户自己的 Key）
  globalQuotes: (symbols: string[]) =>
    getJson<{ ok: boolean; items?: any[]; error?: string }>(
      `/api/global/quotes${qs({ symbols: symbols.join(',') })}`),
  globalKline: (symbol: string, days = 120) =>
    getJson<{ ok: boolean; rows?: any[]; error?: string }>(
      `/api/global/kline${qs({ symbol, days })}`),
  globalIntraday: (symbol: string) =>
    getJson<{ ok: boolean; date?: string | null; points?: { t: string; price: number; volume?: number }[]; error?: string }>(
      `/api/global/intraday${qs({ symbol })}`),

  regime: (days = 60, refresh = false) =>
    getJson<Envelope>(`/api/regime${qs({ days, refresh })}`),

  mainline: (days = 30, refresh = false, realtime = false) =>
    getJson<Envelope>(`/api/mainline${qs({ days, refresh, realtime })}`),

  kphEvents: (date?: string, limit = 60) =>
    getJson<Envelope>(`/api/kph-events${qs({ date, limit })}`),

  kphLive: (date?: string) =>
    getJson<Envelope>(`/api/kph-live${date ? qs({ date }) : ''}`),

  manualFlags: (code: string) =>
    getJson<Envelope>(`/api/manual-flags/${encodeURIComponent(code)}`),

  rps: (days = 12, refresh = false) =>
    getJson<Envelope>(`/api/rps${qs({ days, refresh })}`),

  strategies: () => getJson<{ strategies: StrategyMeta[] }>('/api/strategies'),

  screen: (strategy: string, limit?: number) =>
    getJson<Envelope>(`/api/screen${qs({ strategy, limit })}`),

  ntMonitor: (days = 10) =>
    getJson<Envelope>(`/api/nt-monitor${qs({ days })}`),

  ntEtf: (weeks = 12) =>
    getJson<Envelope>(`/api/nt-etf${qs({ weeks })}`),

  symbolSearch: (q: string) =>
    getJson<{ ok: boolean; items: SearchItem[] }>(
      `/api/symbol-search${qs({ q, limit: 8 })}`,
    ),

  zsxqLatest: () => getJson<Envelope>('/api/zsxq-latest'),

  // 星球纪要汇总（产业资讯页"星球纪要"面板）
  zsxqWeek: (days = 7) =>
    getJson<{ ok: boolean; error?: string; summary?: string; output_dir?: string; days: ZsxqDayStatus[] }>(
      `/api/zsxq/week${qs({ days })}`),

  zsxqReport: (date: string) =>
    getJson<{ ok: boolean; date: string; path?: string; content?: string; chars?: number; error?: string }>(
      `/api/zsxq/report${qs({ date })}`),

  zsxqDigestStart: (days = 7, force = false, date?: string) =>
    postJson<{ ok: boolean; running?: boolean; already_running?: boolean; error?: string }>(
      '/api/zsxq/digest', { days, force, date: date || undefined }),

  zsxqDigestStatus: () =>
    getJson<ZsxqDigestStatus>('/api/zsxq/digest/status'),

  localDocs: () => getJson<{ docs: LocalDoc[] }>('/api/local-docs'),

  localDocDetail: (id: string) =>
    getJson<{ doc: LocalDoc; summary: string | null }>(`/api/local-docs/${id}`),

  uploadLocalDocs: async (files: File[]) => {
    const fd = new FormData()
    files.forEach((f) => fd.append('files', f))
    const res = await fetch('/api/local-docs', { method: 'POST', body: fd })
    const data = await res.json()
    if (!res.ok) throw new Error(data?.error ?? `上传失败 ${res.status}`)
    return data as { saved: LocalDoc[]; rejected: { name: string; reason: string }[] }
  },

  deleteLocalDoc: (id: string) => sendJson<{ ok: boolean }>('DELETE', `/api/local-docs/${id}`),

  localDocRawUrl: (id: string) => `/api/local-docs/${id}/raw`,

  // 纪要股票池（每日纪要重点个股自动累计）
  digestPool: () =>
    getJson<{ stocks: any[]; total: number; unmatched: any[] }>('/api/digest-pool'),

  digestPoolRebuild: (opts: { date?: string; days?: number }) =>
    postJson<any>('/api/digest-pool/rebuild', opts),

  digestPoolRemove: (symbol: string) =>
    sendJson<any>('DELETE', `/api/digest-pool/${symbol}`),

  digestPoolPush: (symbols: string[]) =>
    postJson<any>('/api/digest-pool/push', { symbols }),

  // 自选股（服务端存储，跨设备同步；写操作均返回最新全量 {items, groups}）
  watchlist: () =>
    getJson<{ items: any[]; groups: any[] }>('/api/watchlist'),

  watchlistAddItem: (symbol: string, name?: string, group?: string) =>
    postJson<any>('/api/watchlist/items', { symbol, name, group }),

  watchlistRemoveItem: (symbol: string) =>
    sendJson<any>('DELETE', `/api/watchlist/items/${symbol}`),

  watchlistSetGroup: (symbol: string, group: string) =>
    postJson<any>(`/api/watchlist/items/${symbol}/group`, { group }),

  watchlistAddGroup: (name: string) =>
    postJson<any>('/api/watchlist/groups', { name }),

  watchlistDeleteGroup: (groupId: string) =>
    sendJson<any>('DELETE', `/api/watchlist/groups/${groupId}`),

  watchlistImport: (items: any[], groups: any[]) =>
    postJson<any>('/api/watchlist/import', { items, groups }),

  news: (industry?: string) =>
    getJson<Envelope>(`/api/news${qs({ industry })}`),

  poolTopics: () =>
    getJson<{ data: { preset: string[]; mainline: string[]; as_of: string | null } }>(
      '/api/pool/topics',
    ),

  poolAnalyze: (topic: string, max = 80) =>
    getJson<Envelope>(`/api/pool/analyze${qs({ topic, max })}`),

  limitPools: () => getJson<Envelope>('/api/market/limit-pools'),

  taAnalyze: async (code: string, date?: string) => {
    const res = await fetch(`${BASE}/api/ta/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, date }),
    })
    if (!res.ok) throw new Error(`启动深度分析失败 ${res.status}`)
    return res.json()
  },

  taAnalyzeStatus: (code: string) =>
    getJson<{
      status: 'none' | 'running' | 'done' | 'failed'
      code?: string
      date?: string
      started_at?: string
      finished_at?: string
      result?: import('./types').TaResult
      error?: string
      note?: string
      llm_channel?: 'api' | 'api-fallback'
      llm_model?: string
    }>(`/api/ta/analyze${qs({ code })}`),

  serenityAnalyze: async (code: string) => {
    const res = await fetch(`${BASE}/api/serenity/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    })
    if (!res.ok) throw new Error(`启动 serenity 研究失败 ${res.status}`)
    return res.json()
  },

  serenityStatus: (code: string) =>
    getJson<import('./types').SerenityJob>(`/api/serenity/analyze${qs({ code })}`),

  refreshStatus: () =>
    getJson<{
      running: boolean
      sources: string[]
      started_at: string | null
      finished_at: string | null
      result: unknown
    }>('/api/refresh/status'),

  async refresh(sources: string[]) {
    const res = await fetch(`${BASE}/api/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sources }),
    })
    if (!res.ok) throw new Error(`刷新请求失败 ${res.status}`)
    return res.json()
  },

  // ==================== 推送与告警 ====================

  notifyConfig: () =>
    getJson<{ enabled: boolean; targets: any[]; filter: string[]; review_enabled?: boolean; review_time?: string; review_no_early?: string; content_filters?: Record<string, boolean>; manual_enabled?: boolean; manual_push_time?: string; manual_push_top_n?: number; alert_enabled?: boolean; alert_webhook_url?: string }>('/api/notify/config'),

  notifyTest: (webhookUrl: string) =>
    postJson<any>('/api/notify/test', { webhook_url: webhookUrl }),

  notifyConfigUpdate: (config: any) =>
    postJson<any>('/api/notify/config', config),

  notifyAlertTest: (form?: { alert_enabled?: boolean; alert_webhook_url?: string }) =>
    postJson<{ ok: boolean; error?: string | null }>('/api/notify/alert-test', form ?? {}),

  manualPushTest: () =>
    postJson<{
      ok: boolean; pushed?: boolean; counts?: { trend: number; arb: number } | null
      mode?: string | null; is_empty?: boolean; error?: string | null; reason?: string
    }>('/api/notify/manual-push?force=true', {}),

  notifyDailyReview: () =>
    postJson<any>('/api/notify/daily-review', {}),

  alertRules: () =>
    getJson<{ rules: any[] }>('/api/alert/rules'),

  alertRulesCreate: (rule: any) =>
    postJson<any>('/api/alert/rules', rule),

  alertRulesDelete: (ruleId: number) =>
    sendJson<any>('DELETE', `/api/alert/rules/${ruleId}`),

  alertEvaluate: () =>
    getJson<any>('/api/alert/evaluate'),
}

export const QUICK_SOURCES = ['etf', 'zsxq', 'news'] as const
export const SOURCE_LABELS: Record<string, string> = {
  etf: '国家队 ETF',
  zsxq: '星球纪要',
  news: '全球产业资讯',
  national_team: '国家队持仓',
}