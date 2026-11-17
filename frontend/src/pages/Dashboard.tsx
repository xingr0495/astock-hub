import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Gauge, RefreshCw } from "lucide-react"

import { api } from "../lib/api"
import type { DashboardData } from "../components/dashboard/types"
import { scoreColor, fmtBigNum, fmtPrice } from "../components/dashboard/format"
import { KpiCell, MiniMetric } from "../components/dashboard/KpiCell"
import { IndexTicker } from "../components/dashboard/IndexTicker"
import { DistributionBars, BreadthBar } from "../components/dashboard/Distribution"
import { EmotionCard } from "../components/dashboard/EmotionCard"
import { LadderMini } from "../components/dashboard/LadderMini"
import { StockList } from "../components/dashboard/StockList"
import { HotRank } from "../components/dashboard/HotRank"
import { MembersDialog } from "../components/dashboard/MembersDialog"
import { TurnoverHistoryDialog } from "../components/dashboard/TurnoverHistoryDialog"
import { GlobalQuotes, GlobalQuoteDialog } from "../components/dashboard/GlobalQuotes"
import {
  DashSection, DashboardLayoutEditor, DEFAULT_LAYOUT, useDashboardLayout,
} from "../components/dashboard/layout"
import { useAuth } from "../auth"
import { FundThermometer } from "../components/dashboard/FundThermometer"
import { LatestZsxq } from "../components/dashboard/LatestZsxq"
import { IndustryPoints } from "../components/dashboard/IndustryPoints"
import { ReconcileBadge } from "../components/dashboard/ReconcileBadge"
import { TrendBand } from "../components/dashboard/TrendBand"
import { MainlineBoard } from "../components/dashboard/MainlineBoard"
import { NtMonitor } from "../components/dashboard/NtMonitor"
import { ReviewStatusBar } from "../components/dashboard/ReviewStatusBar"
import { WatchBar } from "../components/dashboard/WatchBar"
import { SessionBanner } from "../components/dashboard/SessionBanner"

export default function Dashboard() {
  const [date, setDate] = useState<string | undefined>()
  const [members, setMembers] = useState<
    { kind: "concept" | "industry"; name: string } | null
  >(null)
  const [turnoverLoaded, setTurnoverLoaded] = useState(false)
  const [turnoverOpen, setTurnoverOpen] = useState(false)
  const [indexDialog, setIndexDialog] = useState<{ symbol: string; label: string } | null>(null)
  // 看板区块自定义（显隐+排序，2026-11-07 用户需求）
  const layoutQ = useDashboardLayout()
  const layout = layoutQ.data ?? DEFAULT_LAYOUT
  const [editLayout, setEditLayout] = useState(false)
  const { isAdmin } = useAuth()

  const q = useQuery({
    queryKey: ["live-dashboard", date, turnoverLoaded],
    queryFn: () => api.dashboard(date, "true", !turnoverLoaded),
    staleTime: 5_000,
    placeholderData: (prev) => prev,
    refetchInterval: (query) => {
      if (date) return false // 历史日不轮询
      const d = query.state.data?.data as unknown as DashboardData | undefined
      return d?.meta.is_trading ? 20_000 : false
    },
    // 看盘时切到别的标签也持续刷新（实时看板预期）
    refetchIntervalInBackground: true,
    onSuccess: (data) => {
      // 首次加载成功后，异步加载换手率数据
      if (!turnoverLoaded) {
        const d = data?.data as unknown as DashboardData | undefined
        if (d && d.meta?.turnover_loaded === false) {
          setTimeout(() => setTurnoverLoaded(true), 1000)
        }
      }
    },
  })

  const raw = q.data?.data as unknown as (DashboardData | { data: { hint: string } }) | undefined
  const x: DashboardData | null =
    raw && "as_of" in (raw as object) ? (raw as DashboardData) : null
  const errorHint = raw && !x ? (raw as { data: { hint: string } }).data?.hint : null

  return (
    <div className="flex flex-col gap-3">
      {/* 顶部条 */}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-surface-700 bg-surface-850 px-3 py-2">
        <div className="flex items-center gap-2">
          <Gauge className="h-4 w-4 text-brand" />
          <h1 className="text-base font-semibold text-slate-100">实时看板</h1>
          {x && (
            <span
              className="rounded-full border px-2 py-0.5 text-[10px] font-medium"
              style={{
                color: scoreColor(x.emotion.score),
                borderColor: `${scoreColor(x.emotion.score)}40`,
                background: `${scoreColor(x.emotion.score)}14`,
              }}
            >
              {x.emotion.label} · {x.emotion.score}
            </span>
          )}
          <ReconcileBadge />
        </div>
        <div className="flex items-center gap-3 text-[11px] text-slate-400">
          <input
            type="date"
            value={date ?? x?.as_of ?? ""}
            max={x?.meta.db_max_date}
            onChange={(e) => setDate(e.target.value || undefined)}
            className="rounded-md border border-surface-700 bg-surface-800 px-2 py-1 text-[11px] text-slate-200"
          />
          {x && (
            <span className="flex items-center gap-1">
              <span
                className={`inline-block h-1.5 w-1.5 rounded-full ${
                  x.meta.is_trading ? "animate-pulse bg-rose-400" : "bg-slate-500"
                }`}
              />
              {x.meta.is_trading ? "盘中实时" : `盘后 · ${x.meta.market_session}`}
            </span>
          )}
          {isAdmin && (
            <button
              onClick={() => setEditLayout((v) => !v)}
              className={`rounded-md border px-2 py-1 text-[11px] ${
                editLayout
                  ? 'border-brand/50 bg-brand/10 text-brand'
                  : 'border-surface-700 bg-surface-800 text-slate-300 hover:text-brand'
              }`}
              title="自定义看板区块的显示与顺序"
            >
              自定义看板
            </button>
          )}
          <button
            onClick={() => q.refetch()}
            disabled={q.isFetching}
            className="inline-flex items-center gap-1 rounded-md border border-surface-700 bg-surface-800 px-2 py-1 text-[11px] text-slate-300 hover:text-slate-100 disabled:opacity-50"
          >
            <RefreshCw className={`h-3 w-3 ${q.isFetching ? "animate-spin" : ""}`} />
            重载
          </button>
          {date && (
            <button
              onClick={() => setDate(undefined)}
              className="inline-flex items-center gap-1 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-300 hover:bg-amber-500/20"
              title="清除日期选择，回到盘中实时"
            >
              ⏱ 回到实时
            </button>
          )}
        </div>
      </div>

      {editLayout && isAdmin && (
        <DashboardLayoutEditor layout={layout} onClose={() => setEditLayout(false)} />
      )}

      {/* 回看模式提示条 */}
      {date && x && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-[11px] text-amber-300">
          ⏱ 正在回看 <span className="font-semibold">{x.as_of}</span> 收盘数据（非实时，各卡片已切换为该日口径）；选周末/无落库日会提示无数据。
        </div>
      )}

      {q.isLoading && (
        <div className="py-20 text-center text-sm text-slate-400">加载市场看板…</div>
      )}
      {errorHint && (
        <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 py-12 text-center text-sm text-rose-300">
          看板加载失败：{errorHint}
        </div>
      )}

      {/* 复盘状态条（C：reviews 落盘链路的看板入口）+ 盘前/非交易日横幅（D2）
          ——均为"今天"的口径，回看模式隐藏 */}
      {!q.isLoading && !errorHint && !date && (
        <>
          <ReviewStatusBar />
          <SessionBanner
            asOf={(x?.meta as any)?.as_of ?? x?.as_of}
            marketSession={(x?.meta as any)?.market_session}
            dbMaxDate={(x?.meta as any)?.db_max_date}
            tradingDay={(x?.meta as any)?.trading_day}
          />
        </>
      )}

      {x && (
        <>
          {/* 指数 */}
          <DashSection id="indices" layout={layout}>
            <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
              {x.indices.map((it) => (
                <IndexTicker key={it.symbol} item={it}
                  onSelect={(item) => setIndexDialog({ symbol: item.symbol, label: item.name })} />
              ))}
            </div>
          </DashSection>

          {/* KPI */}
          <DashSection id="kpi" layout={layout}>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
            <KpiCell
              label="个股涨 / 平 / 跌"
              value={
                <>
                  <span className="text-rose-400">{x.breadth.up}</span>
                  <span className="text-slate-500">/</span>
                  <span className="text-slate-400">{x.breadth.flat}</span>
                  <span className="text-slate-500">/</span>
                  <span className="text-emerald-400">{x.breadth.down}</span>
                </>
              }
              sub={`上涨率 ${x.breadth.up_pct.toFixed(1)}%`}
            />
            <KpiCell
              label="强势 / 弱势"
              value={
                <>
                  <span className="text-rose-400">{x.breadth.strong_up}</span>
                  <span className="text-slate-500">/</span>
                  <span className="text-emerald-400">{x.breadth.strong_down}</span>
                </>
              }
              sub="涨跌 ≥3%"
            />
            <KpiCell
              label="涨停 / 跌停（未封板修正）"
              value={
                <>
                  <span className="text-rose-400">{x.limit.limit_up}</span>
                  <span className="text-slate-500">/</span>
                  <span className="text-emerald-400">{x.limit.limit_down}</span>
                </>
              }
              sub={`封板率 ${x.limit.seal_rate.toFixed(0)}%`}
            />
            <KpiCell
              label="最高连板"
              value={`${x.limit.max_boards}板`}
              tone="accent"
              sub={(() => {
                const top = x.limit.tiers.find((t) => t.boards === x.limit.max_boards)
                const stocks = top?.stocks ?? []
                if (stocks.length > 0 && stocks.length <= 3)
                  return stocks.map((s) => s.name || s.symbol).join(" · ")
                return `梯队 ${x.limit.tiers.length}`
              })()}
            />
            <KpiCell
              label="成交额"
              value={fmtBigNum(x.amount.total)}
              sub={`均额 ${fmtBigNum(x.amount.avg)}`}
              onClick={() => setTurnoverOpen(true)}
              title="点击查看历史成交额"
            />
            <KpiCell
              label="换手 / 量比"
              value={`${x.activity.avg_turnover == null ? "—" : `${fmtPrice(x.activity.avg_turnover, 1)}%`} / ${fmtPrice(x.activity.vol_ratio)}`}
              tone="accent"
              sub={`高换手 ${x.activity.high_turnover} · 放量占比 ${fmtPrice(x.activity.high_vol_ratio, 1)}%`}
            />
          </div>
          </DashSection>

          {/* 趋势带（A：情绪/涨停/上涨占比的近10日位置感） */}
          <DashSection id="trendband" layout={layout}>
            <TrendBand asOf={date} />
          </DashSection>

          {/* 美股/港股（TickFlow 在线行情，点行进详情弹窗） */}
          <DashSection id="global" layout={layout}>
            <GlobalQuotes />
          </DashSection>

          <DashSection id="main" layout={layout}>
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_20rem]">
            <main className="min-w-0 space-y-3">
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
                <section className="rounded-xl border border-surface-700 bg-surface-850 p-3">
                  <div className="mb-2 text-[11px] text-slate-400">
                    涨跌分布 / 广度 · 共 {x.breadth.total} 只
                  </div>
                  <DistributionBars rows={x.distribution} />
                  <div className="mt-3">
                    <BreadthBar data={x.breadth} />
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-1.5">
                    <MiniMetric label="平均涨跌" value={`${(x.breadth.avg_pct * 100).toFixed(2)}%`}
                      cls={x.breadth.avg_pct >= 0 ? "text-rose-400" : "text-emerald-400"} />
                    <MiniMetric label="中位涨跌" value={`${(x.breadth.median_pct * 100).toFixed(2)}%`}
                      cls={x.breadth.median_pct >= 0 ? "text-rose-400" : "text-emerald-400"} />
                  </div>
                </section>

                <section className="rounded-xl border border-surface-700 bg-surface-850 p-3">
                  <div className="mb-1 text-[11px] text-slate-400">情绪周期 · 六维分解</div>
                  <EmotionCard radar={x.radar} score={x.emotion.score} label={x.emotion.label} />
                </section>

                <section className="rounded-xl border border-surface-700 bg-surface-850 p-3">
                  <div className="text-[11px] text-slate-400">趋势强度 · 均线/新高低</div>
                  <div className="mt-2 grid grid-cols-3 gap-1.5">
                    <MiniMetric label="站上MA5" value={`${x.trend.above_ma5_pct.toFixed(0)}%`} cls="text-brand" />
                    <MiniMetric label="站上MA20" value={`${x.trend.above_ma20_pct.toFixed(0)}%`} cls="text-brand" />
                    <MiniMetric label="站上MA60" value={`${x.trend.above_ma60_pct.toFixed(0)}%`} cls="text-brand" />
                    <MiniMetric label="60日新高" value={`${x.trend.new_high}`} cls="text-rose-400" />
                    <MiniMetric label="60日新低" value={`${x.trend.new_low}`} cls="text-emerald-400" />
                    <MiniMetric label="炸板" value={`${x.limit.broken}`} cls="text-amber-400" />
                  </div>
                </section>
              </div>

              <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                <MainlineBoard asOf={date} onShowMembers={(name) => setMembers({ kind: "concept", name })} />
                <HotRank title="行业热度" rank={x.industry_rank}
                  onShowMembers={(name) => setMembers({ kind: "industry", name })} />
              </div>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <StockList title="涨幅榜" rows={x.top_gainers} mode="gain" />
                <StockList title="跌幅榜" rows={x.top_losers} mode="loss" />
                <StockList title="成交额榜" rows={x.turnover_leaders} mode="amount" />
                <StockList title="活跃换手" rows={x.active_leaders} mode="active" />
              </div>
            </main>

            <aside className="min-w-0">
              <section className="rounded-xl border border-surface-700 bg-surface-850 p-3">
                <div className="mb-2 text-sm font-semibold text-slate-100">涨停梯队</div>
                <LadderMini limit={x.limit} />
              </section>
            </aside>
          </div>
          </DashSection>

          {/* 资金温度计 / 最新星球纪要 / 今日产业要点（从大盘总览并入） */}
          <DashSection id="bottom" layout={layout}>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <FundThermometer />
              <LatestZsxq />
              <IndustryPoints />
              <NtMonitor />
            </div>
          </DashSection>

          {/* 我的关注（D·第2期：自选股分组 + 纪要股票池高频） */}
          <DashSection id="watchbar" layout={layout}>
            <WatchBar />
          </DashSection>
        </>
      )}

      {members && (
        <MembersDialog kind={members.kind} name={members.name}
          onClose={() => setMembers(null)} />
      )}
      {indexDialog && (
        <GlobalQuoteDialog
          symbol={indexDialog.symbol}
          label={indexDialog.label}
          onClose={() => setIndexDialog(null)}
        />
      )}
      {turnoverOpen && (
        <TurnoverHistoryDialog
          onClose={() => setTurnoverOpen(false)}
          liveToday={
            !date && x
              ? { date: x.as_of, total: x.amount.total }
              : undefined
          }
        />
      )}
    </div>
  )
}
