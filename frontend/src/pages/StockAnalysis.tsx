import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getInstanceByDom } from 'echarts'
import { api } from '../lib/api'
import { DIMENSION_LABELS, type Envelope, type KlineRow, type SearchItem } from '../lib/types'
import { cleanBlogger } from '../lib/text'
import { useLastStock } from '../lib/queryKeys'
import { useRealtimeQuote } from '../lib/useRealtimeQuote'
import StockSearch from '../components/StockSearch'
import DimensionCard from '../components/DimensionCard'
import KlineChart, { Toggle } from '../components/KlineChart'
import IntradayChart, { type IntradayPayload } from '../components/IntradayChart'
import TaDeepAnalysis from '../components/TaDeepAnalysis'
import SerenityResearch, {
  SerenityDimensionBody,
  useSerenityJob,
} from '../components/SerenityResearch'
import { FinancialSection } from '../components/stock/FinancialSection'
import { DragonTigerSection } from '../components/stock/DragonTigerSection'
import NewsDetailDialog, { type NewsItem } from '../components/NewsDetailDialog'
import { useAuth } from '../auth'

/* ---------- 数据解包工具函数（与后端 Envelope 结构对齐） ---------- */

function dim(env: Envelope | undefined, key: string) {
  return env?.dimensions?.[key]
}

function hithinkSnapshot(bundle?: Envelope) {
  const m = dim(bundle, 'market')
  if (m?.status === 'ok' || m?.status === 'partial') {
    const d = m.data as any
    const hit = d?.sources?.hithink?.data?.item?.[0]
    if (hit) return hit
  }
  return null
}

function klineRows(bundle?: Envelope): KlineRow[] {
  const d = bundle?.data as any
  return d?.data?.rows ?? []
}

function pretty(v: number | null | undefined, digits = 2) {
  return v == null ? '—' : Number(v).toFixed(digits)
}

/**
 * 对容器内所有 echarts 实例补一次 resize（tab 从 display:none 切回时尺寸可能
 * 已变化，不补会空白/拉伸错位）。KlineChart 不透出 ref（任务边界只允许 export
 * Toggle），故走 echarts 官方 getInstanceByDom 遍历 DOM；实例与 echarts-for-react
 * 初始化用的是同一个 echarts 包模块，注册表共享、能查到。
 */
function resizeEchartsIn(el: HTMLElement | null) {
  if (!el) return
  const visit = (node: HTMLElement) => {
    const inst = getInstanceByDom(node)
    if (inst) {
      inst.resize()
      return
    }
    for (const child of Array.from(node.children)) {
      if (child instanceof HTMLElement) visit(child)
    }
  }
  visit(el)
}

/* ---------- 各维度卡片正文 ---------- */

function MarketCardBody({
  bundle,
  kline,
  valuation,
  quotes,
}: {
  bundle?: Envelope
  kline?: Envelope
  valuation?: Envelope
  quotes?: Envelope
}) {
  const shot = hithinkSnapshot(bundle)
  const q = (quotes?.data as any)?.data?.item?.[0] // 同花顺 iFinD 行情（统一口径）
  const rows = klineRows(kline ?? bundle)
  const last = rows[rows.length - 1]
  const v = (valuation?.data as any)?.item?.[0]
  const cross = (quotes?.data as any)?.data?.cross

  const useQ = q && (q.price != null || q.change_pct != null)
  const price = useQ ? q.price : shot?.last_price
  const changePct = useQ ? q.change_pct : shot?.price_change_ratio_pct
  const open = useQ ? q.open : shot?.open_price
  const high = useQ ? q.high : shot?.high_price
  const low = useQ ? q.low : shot?.low_price
  const up = (changePct ?? 0) >= 0

  return (
    <div className="space-y-3">
      {(price != null || shot) && (
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="text-2xl font-semibold text-slate-100">
            {useQ ? pretty(q.price) : shot ? pretty(shot.last_price) : '—'}
          </span>
          <span className={up ? 'text-rose-400' : 'text-emerald-400'}>
            {changePct == null ? '—' : `${changePct >= 0 ? '+' : ''}${Number(changePct).toFixed(2)}%`}
          </span>
          {open != null && (
            <span className="text-xs text-slate-500">
              开 {pretty(open)} / 高 {pretty(high)} / 低 {pretty(low)}
              {shot && ` · 成交 ${(shot.turnover / 1e8).toFixed(2)} 亿`}
            </span>
          )}
          <span className="ml-auto text-[10px] text-slate-600">
            {useQ && (q.time ?? '')}·{useQ ? 'iFinD' : shot ? '同花顺·CLI' : ''}
          </span>
        </div>
      )}

      {cross && (
        <div
          className={`flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded-md border px-2.5 py-1.5 text-[11px] ${
            cross.consistent
              ? 'border-emerald-500/25 bg-emerald-500/5 text-emerald-300'
              : 'border-amber-500/30 bg-amber-500/10 text-amber-300'
          }`}
        >
          <span className="font-medium">交叉验证 · 同花顺 vs 腾讯</span>
          <span>
            {cross.consistent
              ? '现价/涨跌/开高低全部一致 ✓'
              : typeof cross.differ === 'string'
                ? cross.differ
                : `存在差异：${cross.differ.join('、')}`}
          </span>
          <span className="text-[10px] opacity-70">已核对：{cross.matched.join('、')}</span>
        </div>
      )}

      {last && (
        <div className="text-xs text-slate-400">
          均线：MA5 <b className="text-slate-200">{pretty(last.ma5)}</b> · MA10{' '}
          <b className="text-slate-200">{pretty(last.ma10)}</b> · MA20{' '}
          <b className="text-slate-200">{pretty(last.ma20)}</b>
          <span className="ml-1 text-slate-600">（{last.date}）</span>
        </div>
      )}

      {v && (
        <div className="flex flex-wrap gap-2 text-xs">
          <span className="rounded bg-surface-700 px-2 py-1">
            PE(TTM) <b className="text-amber-300">{Number(v.pe_ttm).toFixed(1)}</b>
          </span>
          <span className="rounded bg-surface-700 px-2 py-1">
            PB <b className="text-amber-300">{Number(v.pb_mrq).toFixed(1)}</b>
          </span>
          <span className="rounded bg-surface-700 px-2 py-1">
            PS(TTM) <b className="text-amber-300">{Number(v.ps_ttm).toFixed(1)}</b>
          </span>
        </div>
      )}
    </div>
  )
}

function NationalTeamCardBody({ bundle }: { bundle?: Envelope }) {
  const d = dim(bundle, 'national_team') as any
  const pos = d?.data?.position
  const etf = d?.data?.etf_shares?.data
  return (
    <div className="space-y-2">
      <div>
        <span className="text-[11px] text-slate-500">个股持仓：</span>
        {pos?.status === 'ok' && pos.data ? (
          <span className="text-slate-200">
            {pos.data.count ?? ''} 条持仓记录（季报粒度）
            {pos.data.positions?.[0]?.change_text && ` · ${pos.data.positions[0].change_text}`}
          </span>
        ) : (
          <span className="text-slate-400">监控库未覆盖（十大股东披露有门槛，≠ 没买）</span>
        )}
      </div>
      {etf?.latest && (
        <div className="text-xs text-slate-400">
          汇金宽基合计 <b className="text-slate-200">{Number(etf.latest['全宽基合计'] ?? 0).toFixed(0)}</b> 亿份
          <span
            className={`ml-1 ${(etf.week_over_week_total ?? 0) >= 0 ? 'text-rose-400' : 'text-emerald-400'}`}
          >
            （周环比 {(etf.week_over_week_total ?? 0) > 0 ? '+' : ''}
            {Number(etf.week_over_week_total ?? 0).toFixed(1)} 亿份）
          </span>
        </div>
      )}
    </div>
  )
}

function ZsxqCardBody({ bundle }: { bundle?: Envelope }) {
  const d = dim(bundle, 'zsxq') as any
  // 兼容新旧两代键名：新版 zsxq_mentions / zsxq_latest_report / news_mentions
  const mentions = d?.data?.zsxq_mentions ?? d?.data?.mentions
  const latest = d?.data?.zsxq_latest_report ?? d?.data?.latest_report
  const news = d?.data?.news_mentions ?? d?.data?.news_today
  const related = d?.data?.news_related
  const hits = mentions?.data?.hits ?? []
  const newsHits = news?.data?.hits ?? []
  const tracks: any[] = related?.tracks ?? []
  const localHits: any[] = d?.data?.local_docs_mentions?.data?.hits ?? []
  const [newsItem, setNewsItem] = useState<NewsItem | null>(null)
  return (
    <div className="space-y-2.5">
      <div>
        <span className="text-[11px] text-slate-500">纪要提及：</span>
        {hits.length === 0 ? (
          <span className="text-slate-400">近 7 天纪要无提及（无催化剂 ≠ 看空）</span>
        ) : (
          <div className="mt-1.5 space-y-1.5">
            {hits.slice(0, 4).map((h: any, i: number) => (
              <div key={i} className="rounded-md bg-surface-700/60 px-2.5 py-1.5 text-[12px] text-slate-300">
                <span className="text-[10px] text-slate-500">{h.report_date}</span> {cleanBlogger(h.context)}
              </div>
            ))}
          </div>
        )}
      </div>

      {localHits.length > 0 && (
        <div>
          <span className="text-[11px] text-slate-500">本地资料提及：</span>
          <div className="mt-1.5 space-y-1.5">
            {localHits.slice(0, 4).map((h: any, i: number) => (
              <div key={i} className="rounded-md border border-amber-500/20 bg-amber-500/5 px-2.5 py-1.5 text-[12px] text-slate-300">
                <span className="text-[10px] text-amber-300/80">📁 {h.doc_name ?? '本地资料'} · {h.report_date}</span>
                <div className="text-slate-300">{cleanBlogger(h.context)}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {tracks.length > 0 && (
        <div>
          <span className="text-[11px] text-slate-500">相关产业动态（按行业/概念匹配赛道）：</span>
          <div className="mt-1.5 space-y-2">
            {tracks.map((tr) => (
              <div key={tr.key} className="rounded-md border border-surface-700/70 bg-surface-700/30 px-2.5 py-1.5">
                <div className="mb-1 flex items-center gap-1.5">
                  <span className="rounded bg-brand/15 px-1.5 py-0.5 text-[10px] text-brand">
                    {tr.name}
                  </span>
                  <span className="text-[10px] text-slate-600">AI 今日要点</span>
                </div>
                {(tr.points ?? []).slice(0, 3).map((p: any, i: number) => (
                  <p key={i} className="mb-0.5 text-[11px] leading-relaxed text-slate-300">
                    · {p.t}
                  </p>
                ))}
                {(tr.top_items ?? []).slice(0, 2).map((it: any, i: number) => (
                  <button
                    key={i}
                    type="button"
                    onClick={() => setNewsItem(it)}
                    title="点击查看新闻详情"
                    className="mt-0.5 block w-full truncate text-left text-[11px] text-slate-400 transition-colors hover:text-brand"
                  >
                    <span className="text-slate-600">{String(it.time ?? '').slice(5, 11)} </span>
                    {it.zh || it.title}
                  </button>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}

      {newsHits.length > 0 && (
        <div>
          <span className="text-[11px] text-slate-500">产业新闻命中：</span>
          <div className="mt-1.5 space-y-1.5">
            {newsHits.slice(0, 3).map((h: any, i: number) => (
              <div key={i} className="rounded-md bg-surface-700/60 px-2.5 py-1.5 text-[12px] text-slate-300">
                <span className="text-[10px] text-slate-500">{h.industry ?? h.source ?? ''}</span> {h.title ?? h.zh ?? h.text ?? ''}
              </div>
            ))}
          </div>
        </div>
      )}
      {latest?.data?.excerpt && (
        <p className="line-clamp-3 text-[12px] text-slate-400">
          {cleanBlogger(String(latest.data.excerpt)).replace(/#/g, '').slice(0, 240)}
        </p>
      )}
      {newsItem && <NewsDetailDialog item={newsItem} onClose={() => setNewsItem(null)} />}
    </div>
  )
}

function FundFlowCardBody({ code }: { code: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['fundFlow', code],
    queryFn: () => api.fundFlow(code, 10),
    enabled: !!code,
    staleTime: 60_000,
  })

  if (isLoading) {
    return <div className="text-[12px] text-slate-400">加载资金流向...</div>
  }

  if (error) {
    return <div className="text-[12px] text-slate-500">资金流向暂不可用</div>
  }

  const items = (data?.data as any)?.data?.item ?? []
  if (items.length === 0) {
    return <div className="text-[12px] text-slate-500">暂无资金流向数据</div>
  }

  // 取最近5天
  const recent = items.slice(-5)
  const totalMain = recent.reduce((sum: number, it: any) => sum + (it.main_net ?? 0), 0)
  const totalSuper = recent.reduce((sum: number, it: any) => sum + (it.super_net ?? 0), 0)

  const fmt = (v: number) => {
    const yi = v / 1e8
    return `${yi >= 0 ? '+' : ''}${yi.toFixed(2)}亿`
  }

  return (
    <div className="space-y-2">
      <div className="text-[11px] text-slate-500">近{recent.length}日主力资金</div>
      <div className="flex gap-2 text-[12px]">
        <span className="rounded bg-surface-700 px-2 py-1">
          主力 <b className={totalMain >= 0 ? 'text-rose-300' : 'text-emerald-300'}>{fmt(totalMain)}</b>
        </span>
        <span className="rounded bg-surface-700 px-2 py-1">
          超大单 <b className={totalSuper >= 0 ? 'text-rose-300' : 'text-emerald-300'}>{fmt(totalSuper)}</b>
        </span>
      </div>
      <div className="space-y-1">
        {recent.map((it: any, i: number) => (
          <div key={i} className="flex items-center justify-between text-[11px]">
            <span className="text-slate-500">{it.time?.slice(5) ?? ''}</span>
            <span className={it.main_net >= 0 ? 'text-rose-400' : 'text-emerald-400'}>
              {fmt(it.main_net ?? 0)}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ---------- 个股五维分析页 ---------- */

export default function StockAnalysis() {
  const { isAdmin } = useAuth()
  const [params] = useSearchParams()
  const urlCode = params.get('code') ?? ''
  const [lastStock, storeLastStock] = useLastStock()
  const [code, setCode] = useState<string>(urlCode || lastStock)
  const [label, setLabel] = useState<string>(urlCode ? '' : lastStock)

  const query = useQuery({
    queryKey: ['stock', code],
    queryFn: () =>
      Promise.all([api.bundle(code), api.kline(code, 120), api.valuation(code), api.quotes(code)]),
    enabled: !!code,
    staleTime: 5 * 60_000,
  })

  const [bundle, kline, valuation, quotes] = query.data ?? [undefined, undefined, undefined, undefined]

  // 产业链研究：五维格与下方大卡共用这一份任务（名称由后端从数据源解析）
  // 权限闸：/api/serenity/* 为 admin 级端点——非管理员传空 code 禁查询（消挂载即轮询的 403），大卡同闸不渲染
  const serenity = useSerenityJob(isAdmin ? code : '')



  // 走势区 tab：分时 | 日K（默认日K，与改版前一致）
  const [chartTab, setChartTab] = useState<'intraday' | 'kline'>('kline')
  const klinePaneRef = useRef<HTMLDivElement>(null)
  const intradayPaneRef = useRef<HTMLDivElement>(null)
  const dimGridRef = useRef<HTMLDivElement>(null)

  // 实时行情：盘中用同花顺同源实时价覆盖静态收盘价格（5 秒自动刷新）。
  // 声明位置有讲究：isTrading 同时是下方分时轮询的交易时段闸门，而 react-query v5
  // 的 Observer 构造函数会同步 setOptions 并无条件求值 refetchInterval
  // （queryObserver.js #computeRefetchInterval），闭包若引用声明在后的变量，首渲染
  // 就撞暂时性死区（挂载即 ReferenceError）——故必须整体挪到分时 useQuery 之前。
  const tsCode = (bundle?.query?.ts_code as string) ?? undefined
  const { tick: realtime, isTrading } = useRealtimeQuote(tsCode)

  // 分时独立查询：懒加载（切到分时 tab 才发请求）、30s 内新鲜、切回时保留占位
  // 防闪烁；仅「交易时段 + status ok + is_today」盘中 60s 轮询——isTrading 取自上方
  // useRealtimeQuote（/api/quote 的 is_trading，后端口径含节假日），盘前/盘后/节假日
  // 自动停轮询（已有数据仍正常展示）。quote 5s 轮询驱动组件重渲染 → options 每轮
  // 重建，闸门翻转后最迟 5s 内生效。
  const intraday = useQuery({
    queryKey: ['intraday', code],
    queryFn: () => api.intraday(code),
    enabled: chartTab === 'intraday' && !!code,
    staleTime: 30_000,
    placeholderData: (prev) => prev,
    refetchInterval: (query) => {
      const d = query.state.data?.data as
        | { status?: string; data?: { is_today?: boolean } }
        | undefined
      return isTrading && d?.status === 'ok' && d?.data?.is_today === true ? 60_000 : false
    },
  })

  // 刚显示的一侧可能是隐藏期间变过尺寸（或首次以 0 尺寸渲染），补一次 resize
  useEffect(() => {
    resizeEchartsIn(chartTab === 'kline' ? klinePaneRef.current : intradayPaneRef.current)
  }, [chartTab])

  // 信息面卡片高度 = 上排三张卡片高度之和（含两行间距）：桌面 xl（三列、
  // 信息面独占第二行且跨满三列）时同步，超长内容在卡内滚动；
  // 窄屏单列/两列恢复自然高度（两列时信息面与国家队同行，网格自动等高）。
  useEffect(() => {
    const grid = dimGridRef.current
    if (!grid) return
    const zsxqCard = grid.querySelector<HTMLElement>('.dim-card-zsxq')
    const cards = grid.querySelectorAll<HTMLElement>('[data-dim-card]')
    if (!zsxqCard || cards.length < 3) return
    const xl = window.matchMedia('(min-width: 1280px)')
    const apply = () => {
      if (xl.matches) {
        zsxqCard.style.height = ''  // 先清约束再测量，避免反向影响
        const gap = parseFloat(getComputedStyle(grid).rowGap || '0') || 0
        const top = [...cards].slice(0, 3)
        const total = top.reduce((sum, c) => sum + c.getBoundingClientRect().height, 0)
          + gap * (top.length - 1)
        zsxqCard.style.height = `${total}px`
      } else {
        zsxqCard.style.height = ''
      }
    }
    apply()
    const ro = new ResizeObserver(apply)
    ;[...cards].slice(0, 3).forEach((c) => ro.observe(c))  // 只看上排，避免高度自激循环
    xl.addEventListener?.('change', apply)
    window.addEventListener('resize', apply)
    const t = window.setTimeout(apply, 1200) // 等资金流等异步内容撑开上排
    return () => {
      ro.disconnect()
      xl.removeEventListener?.('change', apply)
      window.removeEventListener('resize', apply)
      window.clearTimeout(t)
    }
  }, [code, bundle])

  // 分时响应解包：Envelope.data 是 DimensionResult 形状 {status, data, error}
  const intradayBody = intraday.data?.data as
    | {
        status?: string
        data?: (IntradayPayload & { hint?: string }) | null
        error?: { message?: string } | null
      }
    | undefined
  const intradayPayload =
    intradayBody?.status === 'ok' && Array.isArray(intradayBody.data?.points) && intradayBody.data!.points.length > 0
      ? (intradayBody.data as IntradayPayload)
      : undefined
  const klineOk = !!kline?.data && (kline.data as any).status === 'ok'

  const price = useMemo(() => {
    // 行情口径统一：iFinD（同花顺）→ hithink 快照 → K线末位
    const q = (quotes?.data as any)?.data?.item?.[0]
    if (q && (q.price != null || q.change_pct != null) && q.price != null) {
      return { v: Number(q.price), pct: Number(q.change_pct ?? 0) }
    }
    const shot = hithinkSnapshot(bundle)
    const rows = klineRows(bundle)
    if (shot) {
      return { v: Number(shot.last_price), pct: Number(shot.price_change_ratio_pct) }
    }
    const last = rows[rows.length - 1]
    return last ? { v: last.close, pct: Number(last.change_pct) * 100 } : null
  }, [bundle, quotes])

  const isUp = (price?.pct ?? 0) >= 0

  // 实时价覆盖（tsCode / isTrading 已在上方分时查询前声明，供轮询闸门复用）
  const livePrice = realtime ? { v: realtime.price, pct: realtime.changePct } : price
  const liveUp = (livePrice?.pct ?? 0) >= 0

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex-1">
          <StockSearch
            onSelect={(it: SearchItem) => {
              setCode(it.ticker)
              setLabel(it.name)
              storeLastStock(it.ticker)
            }}
          />
        </div>
      </div>

      {!code && (
        <div className="rounded-xl border border-dashed border-surface-600 bg-surface-850/50 py-16 text-center text-sm text-slate-500">
          在上方输入股票代码或名称，开始五维分析
          <br />
          <span className="text-xs text-slate-600">例如 002837（英维克）、600519（贵州茅台）、600172（黄河旋风）</span>
        </div>
      )}

      {code && query.isLoading && (
        <div className="py-16 text-center text-sm text-slate-400">正在并行拉取五维数据（行情/国家队/纪要/环境）…</div>
      )}

      {code && query.isError && (
        <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 py-16 text-center text-sm text-rose-300">
          查询失败：{query.error instanceof Error ? query.error.message : String(query.error)}
        </div>
      )}

      {code && query.data && (
        <>
          <header className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <h1 className="text-xl font-semibold text-slate-100">
              {bundle?.query?.name ?? label}{' '}
              <span className="text-sm font-normal text-slate-500">{bundle?.query?.code ?? code}</span>
            </h1>
            {livePrice && (
              <>
                <span className="text-2xl font-semibold text-slate-100">
                  {livePrice.v.toFixed(2)}
                  {isTrading && (
                    <span className="ml-1.5 inline-block h-2 w-2 animate-pulse rounded-full bg-rose-500 align-middle" title="实时行情" />
                  )}
                </span>
                <span
                  className={`text-base font-medium ${liveUp ? 'text-rose-400' : 'text-emerald-400'}`}
                >
                  {liveUp ? '+' : ''}
                  {livePrice.pct.toFixed(2)}%
                </span>
                {isTrading && (
                  <span className="rounded bg-rose-500/15 px-1.5 py-0.5 text-[10px] text-rose-300">
                    实时 · 5s
                  </span>
                )}
              </>
            )}
            <div className="ml-auto grid grid-cols-2 gap-1 text-center text-[10px] text-slate-500 sm:grid-cols-5">
              {Object.entries(bundle?.summary ?? {}).map(([k, n]) => (
                <div key={k} className="rounded bg-surface-850 px-1.5 py-1">
                  {k}:<span className="text-slate-300">{n}</span>
                </div>
              ))}
            </div>
          </header>


          {/* 走势：分时 | 日K tab 容器——两图都保持挂载、非活动侧 display:none
              （保证来回切不丢 K 线内部状态/不闪烁）；日K 原有 error 分支保留在
              日K侧，分时独立降级互不影响 */}
          <section className="rounded-xl border border-surface-700 bg-surface-850 p-3">
            <div className="mb-1 flex flex-wrap items-center gap-1.5 px-1 text-[11px]">
              <Toggle active={chartTab === 'intraday'} onClick={() => setChartTab('intraday')}>
                分时
              </Toggle>
              <Toggle active={chartTab === 'kline'} onClick={() => setChartTab('kline')}>
                日K
              </Toggle>
              <span className="ml-1 text-slate-500">
                {chartTab === 'intraday'
                  ? intradayPayload
                    ? `${intradayPayload.source === 'axdata' ? '通达信' : '腾讯'}分时 · ${intradayPayload.date}`
                    : '腾讯分时'
                  : klineOk
                    ? `走势：${(kline!.data as any).data?.source ?? '—'} · 主图可切均线/BOLL，副图可切成交量/MACD/KDJ/量比`
                    : ''}
              </span>
            </div>
            <div ref={klinePaneRef} style={chartTab === 'kline' ? undefined : { display: 'none' }}>
              {klineOk ? (
                <KlineChart rows={klineRows(kline)} />
              ) : (
                <div className="p-1 text-sm text-slate-400">
                  走势数据不可用：{(kline?.data as any)?.data?.hint ?? (kline?.error as any)?.message ?? '未返回日K序列'}
                </div>
              )}
            </div>
            <div ref={intradayPaneRef} style={chartTab === 'intraday' ? undefined : { display: 'none' }}>
              {intradayPayload ? (
                <IntradayChart payload={intradayPayload} />
              ) : intraday.isLoading ? (
                <div className="py-16 text-center text-sm text-slate-400">加载分时…</div>
              ) : (
                <div className="py-6 text-center text-sm text-slate-400">
                  {intraday.error instanceof Error
                    ? `分时加载失败：${intraday.error.message}`
                    : (intradayBody?.data as { hint?: string } | undefined)?.hint ??
                      intradayBody?.error?.message ??
                      '分时数据不可用'}
                  <div className="mt-1 text-xs text-slate-500">分时暂不可用，可切回日K——日K区不受影响</div>
                </div>
              )}
            </div>
          </section>

          {/* 五维卡片 */}
          <div ref={dimGridRef} className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {Object.entries(DIMENSION_LABELS).map(([key, { icon, title }]) => {
              switch (key) {
                case 'market':
                  return (
                    <DimensionCard key={key} icon={icon} title={title} dim={dim(bundle, key)}>
                      <MarketCardBody bundle={bundle} kline={kline} valuation={valuation} quotes={quotes} />
                    </DimensionCard>
                  )
                case 'fund_flow':
                  return (
                    <DimensionCard key={key} icon={icon} title={title} dim={dim(bundle, key)}>
                      <FundFlowCardBody code={code} />
                    </DimensionCard>
                  )
                case 'national_team':
                  return (
                    <DimensionCard key={key} icon={icon} title={title} dim={dim(bundle, key)}>
                      <NationalTeamCardBody bundle={bundle} />
                    </DimensionCard>
                  )
                case 'zsxq':
                  return (
                    <DimensionCard key={key} className="dim-card-zsxq md:col-span-2 xl:col-span-3" icon={icon} title={title} dim={dim(bundle, key)}>
                      <ZsxqCardBody bundle={bundle} />
                    </DimensionCard>
                  )
                default:
                  return null
              }
            })}
          </div>

          {/* 产业链深度研究（serenity-skill 方法论，LLM 快速扫描）——与上方五维格共用同一份任务；仅管理员 */}
          {isAdmin && <SerenityResearch {...serenity} />}

          {/* 深度多 Agent 分析（TradingAgents-astock，长任务按需触发）——权限闸：仅管理员 */}
          {isAdmin && <TaDeepAnalysis code={bundle?.query?.code ?? code} />}

          {/* 财务报表（估值/业绩预告/研报） */}
          <FinancialSection code={bundle?.query?.code ?? code} name={bundle?.query?.name as string} />

          {/* 龙虎榜 */}
          <DragonTigerSection code={bundle?.query?.code ?? code} name={bundle?.query?.name as string} />

          <footer className="rounded-xl border border-surface-700 bg-surface-850 p-4 text-[11px] leading-relaxed text-slate-500">
            数据截止：{bundle.generated_at?.replace('T', ' ').slice(0, 16)}（行情快照可能含当日实时数据）
            <br />
            五维体系提升概率但不保证盈利；stale / unavailable 的维度已标注、不计入共振。本面板仅供研究参考，不构成投资建议，决策请自行判断并承担风险。
          </footer>
        </>
      )}
    </div>
  )
}