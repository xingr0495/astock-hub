import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import ReactECharts from 'echarts-for-react'
import { api } from '../lib/api'
import { cleanBlogger } from '../lib/text'
import type { Envelope } from '../lib/types'
import RefreshButton from '../components/RefreshButton'
import RegimeSection from '../components/RegimeSection'
import MainlineSection from '../components/MainlineSection'
import NewsDetailDialog, { type NewsItem } from '../components/NewsDetailDialog'

const INDEX_NAMES: Record<string, string> = {
  '000001.SH': '上证指数',
  '399001.SZ': '深证成指',
  '399006.SZ': '创业板指',
  '000688.SH': '科创50',
  '000300.SH': '沪深300',
  '000905.SH': '中证500',
}

const pct = (v: number | null | undefined, digits = 2) =>
  v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(digits)}%`

const num = (v: number | null | undefined, digits = 2) =>
  v == null ? '—' : Number(v).toFixed(digits)

/**
 * 大盘复盘（市场总览）：数据全部来自 同花顺 iFinD / 国家队 / 星球纪要 / 全球产业新闻，
 * 不依赖 tickflow（暂时停用）。
 */
export default function MarketOverview() {
  const [selectedNews, setSelectedNews] = useState<NewsItem | null>(null)
  const query = useQuery({
    queryKey: ['market'],
    queryFn: () =>
      Promise.all([api.indexes(), api.limitPools(), api.ntEtf(12), api.zsxqLatest(), api.news()]),
    staleTime: 2 * 60_000,
  })

  const [indexes, limitPools, ntEtf, zsxq, news] = useMemo(
    () => (query.data ?? [undefined, undefined, undefined, undefined, undefined]) as [
      Envelope | undefined,
      Envelope | undefined,
      Envelope | undefined,
      Envelope | undefined,
      Envelope | undefined,
    ],
    [query.data],
  )
  const newsTitles = useMemo(() => {
    const newsData = (news?.data as any)?.data as any
    const inds: any[] = newsData?.industries ?? []
    return inds.length && inds[0]?.top_items
      ? (inds[0].top_items as { title: string; zh?: string; source?: string; time?: string }[]).slice(0, 6)
      : inds
          .flatMap((i: any) => i.points ?? [])
          .map((p: any) => ({ title: p.t ?? '', source: '', time: '', zh: '' }))
          .slice(0, 6)
  }, [news])

  if (query.isLoading) {
    return <div className="py-16 text-center text-sm text-slate-400">正在汇总大盘数据…</div>
  }
  if (query.isError) {
    return (
      <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 py-16 text-center text-sm text-rose-300">
        加载失败：{query.error instanceof Error ? query.error.message : String(query.error)}
      </div>
    )
  }

  // 注意 envelope 取值：/api/indexes 与 /api/market/limit-pools 直接放在 data 字段；
  // 其余（nt-etf/zsxq/news/kline/quotes）是 data 包一层 DimensionResult
  const idxData = (indexes?.data as any) as any
  const lp = (limitPools?.data as any) as any
  const breadth = lp?.breadth
  const lpUp = lp?.up
  const lpDown = lp?.down
  const lpUpRows: { symbol: string; name: string }[] = lpUp?.rows ?? []
  const useIFindPools = lp?.sources?.up === 'ifind' && lpUpRows.length > 0
  // 盘前/非交易时段：实时名单没生成，后端用最近交易日计数补齐（rows 仍空）
  const realtime = lp?.realtime !== false
  const latestTradeDate: string | null = lp?.latest_trade_date ?? null
  const etf = (ntEtf?.data as any)?.data as any
  const series = etf?.series ?? []
  const zsxqData = (zsxq?.data as any)?.data as any
  const newsData = (news?.data as any)?.data as any

  const asOf =
    (idxData?.as_of as string) ??
    (lp?.as_of as string) ??
    (etf?.latest?.date as string) ??
    '—'

  const etfSparkOption = {
    backgroundColor: 'transparent',
    grid: { left: 4, right: 4, top: 8, bottom: 4 },
    xAxis: { type: 'category', show: false, data: series.map((s: any) => s.date) },
    yAxis: { type: 'value', show: false, scale: true },
    series: [
      {
        type: 'line',
        data: series.map((s: any) => s['全宽基合计']),
        smooth: true,
        symbol: 'none',
        lineStyle: { color: '#38bdf8', width: 1.5 },
        areaStyle: { color: 'rgba(56,189,248,0.12)' },
      },
    ],
  }

  const newsIndLabel = newsData?.industries?.[0]?.name ?? '全球产业新闻'

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">大盘总览</h1>
          <div className="mt-1 text-xs text-slate-500">
            数据截至 {asOf}
          </div>
        </div>
        <RefreshButton />
      </header>

      {/* 市场情绪周期 + 今日主线（本地计算，不依赖 tickflow） */}
      <RegimeSection />
      <MainlineSection />

      {/* 指数卡片（iFinD） */}
      <section>
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="text-sm font-semibold text-slate-100">主要指数</h2>
          
        </div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          {(idxData?.item ?? []).map((it: any) => {
            const up = (it.price_change_ratio_pct ?? 0) >= 0
            return (
              <div key={it.thscode} className="rounded-xl border border-surface-700 bg-surface-850 p-3">
                <div className="text-xs text-slate-400">{INDEX_NAMES[it.thscode] ?? it.thscode}</div>
                <div className="mt-1 text-lg font-semibold text-slate-100">{num(it.last_price, 2)}</div>
                <div className={`text-xs font-medium ${up ? 'text-rose-400' : 'text-emerald-400'}`}>
                  {pct(it.price_change_ratio_pct)}
                </div>
                {it.turnover != null && (
                  <div className="mt-0.5 text-[10px] text-slate-500">
                    成交 {(it.turnover / 1e8).toFixed(0)} 亿
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </section>

      {/* 涨跌停 + 市场宽度（iFinD） */}
      <div className="grid gap-4 xl:grid-cols-3">
        <section className="rounded-xl border border-surface-700 bg-surface-850 p-4 xl:col-span-1">
          <header className="mb-3 flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold text-slate-100">市场宽度</h2>
            <span className="ml-auto rounded bg-surface-700 px-2 py-0.5 text-[10px] text-slate-400">
              沪深京
            </span>
          </header>
          <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-[13px]">
            <Stat k="上涨家数" v={breadth?.up ?? '—'} up />
            <Stat k="下跌家数" v={breadth?.down ?? '—'} />
            <Stat k="涨停" v={`${(lpUp?.count ?? breadth?.limit_up) ?? '—'} 只`} up />
            <Stat k="跌停" v={`${(lpDown?.count ?? breadth?.limit_down) ?? '—'} 只`} />
            <Stat k="两市成交" v={breadth?.amount != null ? `${(breadth.amount / 1e12).toFixed(2)} 万亿` : '—'} />
            <Stat k="停牌" v={breadth?.suspended ?? '—'} />
          </div>
          <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
            其中涨停/跌停、上涨/下跌家数含北交所，与行情软件收盘口径一致。
          </p>
        </section>

        {/* 涨停池（iFinD） */}
        <section className="rounded-xl border border-surface-700 bg-surface-850 p-4 xl:col-span-2">
          <header className="mb-3 flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold text-slate-100">
              涨停池 <span className="text-xs text-slate-500">（共 {lpUp?.count ?? '—'} 只）</span>
            </h2>
            <span
              className={`ml-auto rounded px-1.5 py-0.5 text-[10px] ${
                lpUpRows.length > 0 ? 'bg-brand/15 text-brand'
                : realtime ? 'bg-amber-500/15 text-amber-300'
                : 'bg-sky-500/15 text-sky-300'
              }`}
            >
              {lpUpRows.length > 0
                ? '实时'
                : realtime ? '生成中' : `最近交易日 ${latestTradeDate?.slice(5) ?? ''}`}
            </span>
          </header>
          {lpUpRows.length > 0 ? (
            <div className="max-h-80 space-y-1.5 overflow-y-auto pr-1">
              {lpUpRows.map((r) => (
                <Link
                  key={r.symbol}
                  to={`/?code=${r.symbol.split('.')[0]}`}
                  className="flex items-center justify-between rounded-md bg-surface-700/50 px-2.5 py-1.5 text-[12px] transition-colors hover:bg-surface-700/80"
                  title="跳转五维个股分析"
                >
                  <span className="flex items-center gap-2">
                    <span className="w-24 truncate text-slate-200">{r.name}</span>
                    <span className="text-[10px] text-slate-500">{r.symbol.split('.')[0]}</span>
                  </span>
                  <span className="rounded bg-rose-500/15 px-1.5 py-0.5 text-[10px] text-rose-300">涨停</span>
                </Link>
              ))}
            </div>
          ) : realtime ? (
            <p className="py-8 text-center text-xs text-slate-500">涨停池数据暂不可用（可稍后刷新）</p>
          ) : (
            <p className="py-8 text-center text-xs leading-relaxed text-slate-400">
              当前为盘前/非交易时段，实时涨停名单尚未生成。
              <br />
              最近交易日 <b className="text-slate-200">{latestTradeDate}</b> 涨停{' '}
              <b className="text-rose-300">{lpUp?.count ?? 0}</b> 只 · 跌停{' '}
              <b className="text-emerald-300">{lpDown?.count ?? 0}</b> 只
              <br />
              <span className="text-[11px] text-slate-500">个股名单盘后提供；上方「市场情绪周期」可看完整统计</span>
            </p>
          )}
          {lpDown && (lpDown.rows ?? []).length > 0 && (
            <div className="mt-2 border-t border-surface-700/60 pt-2">
              <div className="mb-1 text-[11px] text-slate-500">跌停（{lpDown.count}）</div>
              <div className="flex flex-wrap gap-1.5">
                {(lpDown.rows ?? []).slice(0, 20).map((r: any) => (
                  <span key={r.symbol} className="rounded bg-surface-700/60 px-2 py-0.5 text-[11px] text-emerald-300">
                    {r.name}
                  </span>
                ))}
              </div>
            </div>
          )}
        </section>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        {/* 汇金 ETF 温度计（国家队） */}
        <section className="rounded-xl border border-surface-700 bg-surface-850 p-4">
          <header className="mb-2 flex items-center gap-2">
            <h2 className="text-sm font-semibold text-slate-100">资金温度计</h2>
            <span className="ml-auto rounded bg-surface-700 px-2 py-0.5 text-[11px] text-slate-400">
              汇金宽基份额
            </span>
          </header>
          <div className="h-16">
            <ReactECharts option={etfSparkOption} style={{ height: '100%' }} notMerge />
          </div>
          {etf?.latest && (
            <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-slate-400">
              {(['上证50', '沪深300', '中证500', '中证1000', '科创50', '中证A500'] as const).map((k) => (
                <div key={k} className="flex justify-between">
                  <span>{k}</span>
                  <b className="text-slate-200">{Math.round(etf.latest[k] ?? 0)}亿</b>
                </div>
              ))}
            </div>
          )}
          <div className="mt-2 text-[11px] text-slate-500">
            合计 {(etf?.latest?.['全宽基合计'] ?? 0).toFixed(0)} 亿份 · 周环比{' '}
            <span className={(etf?.week_over_week_total ?? 0) >= 0 ? 'text-rose-400' : 'text-emerald-400'}>
              {(etf?.week_over_week_total ?? 0) >= 0 ? '+' : ''}
              {(etf?.week_over_week_total ?? 0).toFixed(1)} 亿份
            </span>
          </div>
        </section>

        <div className="space-y-4 xl:col-span-2">
          {/* 最新星球纪要 */}
          <section className="rounded-xl border border-surface-700 bg-surface-850 p-4">
            <header className="mb-2 flex items-center gap-2">
              <h2 className="text-sm font-semibold text-slate-100">最新星球纪要</h2>
              <span className="ml-auto text-[10px] text-slate-500">
                {zsxqData?.date ?? ''}
              </span>
            </header>
            <p className="line-clamp-6 whitespace-pre-line text-[12px] leading-relaxed text-slate-400">
              {cleanBlogger(zsxqData?.excerpt).slice(0, 520) ?? '暂无纪要数据'}
            </p>
          </section>

          {/* 全球产业新闻·今日要点 */}
          <section className="rounded-xl border border-surface-700 bg-surface-850 p-4">
            <header className="mb-2 flex items-center gap-2">
              <h2 className="text-sm font-semibold text-slate-100">今日产业要点</h2>
              <span className="ml-auto text-[10px] text-slate-500">{newsIndLabel}</span>
            </header>
            {newsTitles.length === 0 ? (
              <p className="text-xs text-slate-500">暂无产业新闻要点</p>
            ) : (
              <ul className="space-y-1.5">
                {newsTitles.map((n, i) => (
                  <li key={i} className="text-[12px] text-slate-300">
                    <button
                      className="flex w-full items-center gap-1.5 rounded px-0.5 py-0.5 text-left hover:bg-surface-700/50"
                      onClick={() => setSelectedNews(n as NewsItem)}
                      title="查看详情"
                    >
                      <span className="shrink-0 text-[10px] text-slate-500">{n.time ?? ''}</span>
                      <span className="truncate hover:text-brand">{n.zh || n.title}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>

      {selectedNews && (
        <NewsDetailDialog item={selectedNews} onClose={() => setSelectedNews(null)} />
      )}
    </div>
  )
}

function Stat({ k, v, up }: { k: string; v: string | number; up?: boolean }) {
  return (
    <div className="flex items-baseline justify-between border-b border-surface-700/50 pb-1">
      <span className="text-slate-500">{k}</span>
      <b className={up ? 'text-rose-300' : 'text-slate-200'}>{v}</b>
    </div>
  )
}