import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { sessionRefreshMs, useMinuteTick } from '../lib/refresh'
import { pctClass, fmtIndexPct } from '../components/dashboard/format'
import RotationHistoryDialog from '../components/RotationHistoryDialog'
import { MembersDialog } from '../components/dashboard/MembersDialog'
import { RpsHeatmap } from '../components/RpsHeatmap'
import {
  classifyLatest,
  localDateStr,
  MAINLINE_BADGES,
  type MainRow,
  type ClassifiedRow,
} from '../lib/mainline'
import { TrendingUp, BarChart3 } from 'lucide-react'

type Tab = 'rotation' | 'rps' | 'fundflow'
/** 仅资金 Tab 使用（东财/开盘红板块桶）；轮动/RPS Tab 的行 kind 恒为 concept|industry。 */
type BoardType = 'selected' | 'industry'
/** 主线榜单行：lib 的 MainRow 未含龙头字段，本页局部扩展（API 行实际带 leader_symbol）。 */
type RotRow = MainRow & { leader_symbol?: string | null }
type ClsRotRow = ClassifiedRow & { leader_symbol?: string | null }

export default function SectorAnalysis() {
  const [tab, setTab] = useState<Tab>('rotation')
  const [boardType, setBoardType] = useState<BoardType>('selected')
  const [selectedSector, setSelectedSector] = useState<{ kind: string; name: string } | null>(null)

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <h1 className="text-lg font-semibold text-slate-100">板块分析</h1>
        <div className="flex gap-1 rounded-lg bg-surface-800 p-1">
          <button
            onClick={() => setTab('rotation')}
            className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
              tab === 'rotation' ? 'bg-brand/20 text-brand' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <TrendingUp className="mr-1 inline h-4 w-4" />
            板块轮动
          </button>
          <button
            onClick={() => setTab('rps')}
            className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
              tab === 'rps' ? 'bg-brand/20 text-brand' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <TrendingUp className="mr-1 inline h-4 w-4" />
            涨幅轮动
          </button>
          <button
            onClick={() => setTab('fundflow')}
            className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
              tab === 'fundflow' ? 'bg-brand/20 text-brand' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <BarChart3 className="mr-1 inline h-4 w-4" />
            资金流向
          </button>
        </div>
      </div>

      {tab === 'rotation' ? (
        <SectorRotation onSelectSector={setSelectedSector} />
      ) : tab === 'rps' ? (
        <RpsHeatmap onSelectSector={setSelectedSector} />
      ) : (
        <SectorFundFlow boardType={boardType} onBoardTypeChange={setBoardType} onSelectSector={setSelectedSector} />
      )}

      {/* 成分股弹窗统一走看板 MembersDialog（realtime=true + 盘中 30s 轮询 + 点个股跳分析页） */}
      {selectedSector && (
        <MembersDialog
          kind={selectedSector.kind as 'concept' | 'industry'}
          name={selectedSector.name}
          onClose={() => setSelectedSector(null)}
        />
      )}
    </div>
  )
}

function SectorRotation({ onSelectSector }: { onSelectSector: (s: { kind: string; name: string }) => void }) {
  useMinuteTick()
  const navigate = useNavigate()
  const [showHistory, setShowHistory] = useState(false)
  const { data, isLoading } = useQuery({
    queryKey: ['mainline'],
    queryFn: () => api.mainline(7, false, true),
    staleTime: 60_000,
    refetchInterval: sessionRefreshMs(),
  })

  if (isLoading) {
    return <div className="text-sm text-slate-400">加载板块轮动数据...</div>
  }

  const payload = (data?.data as any) ?? {}
  const kinds = payload.kinds ?? {}
  const concepts: RotRow[] = kinds.concept ?? []
  const industries: RotRow[] = kinds.industry ?? []
  const rt = payload.realtime as { rt_time?: string; intraday?: boolean } | undefined

  // 取最近一天的数据（概念/行业同快照同日期）
  const latestDate = concepts.length > 0 ? concepts[concepts.length - 1]?.date : null
  const isLive = !!rt?.intraday && latestDate === localDateStr()
  const stamp = isLive
    ? `盘中实时 ${rt?.rt_time?.slice(11, 16) ?? ''}`
    : (latestDate ?? '').slice(5)

  const renderList = (rows: RotRow[], kind: 'concept' | 'industry') => {
    const latest = rows.filter((c) => c.date === latestDate)
    const clsRows = classifyLatest(latest, rows) as ClsRotRow[]
    // 近 5 日上榜圆点的日期窗口（与 title 提示的 presence 共用）
    const recentDates = [...new Set(rows.map((r) => r.date))].sort().slice(-5)
    return clsRows.slice(0, 10).map((item) => {
      const leader = item.leader_symbol
      return (
        <div
          key={item.member}
          onClick={() => onSelectSector({ kind, name: item.member })}
          className="flex cursor-pointer items-center justify-between rounded-lg bg-surface-800 px-3 py-2 transition-colors hover:bg-surface-700"
        >
          <div className="flex items-center gap-3">
            <span className={`w-6 text-center text-sm font-bold ${
              item.rank <= 3 ? 'text-amber-400' : 'text-slate-500'
            }`}>
              {item.rank}
            </span>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-sm text-slate-200">{item.member}</span>
                <span className={`rounded border px-1 py-px text-[9px] ${MAINLINE_BADGES[item.cls].cls}`}>
                  {MAINLINE_BADGES[item.cls].text}
                </span>
              </div>
              <div className="text-[11px] text-slate-500">
                涨停 {item.limit_up_count} · 最高 {item.max_boards}板
                <span className="ml-2 inline-flex gap-0.5 align-middle" title={`近5日上榜 ${item.presence} 天`}>
                  {recentDates.map((d) => (
                    <span
                      key={d}
                      className={`inline-block h-1.5 w-1.5 rounded-full ${
                        rows.some((x) => x.date === d && x.member === item.member)
                          ? 'bg-brand'
                          : 'bg-surface-600'
                      }`}
                    />
                  ))}
                </span>
              </div>
            </div>
          </div>
          <div className="text-right">
            {leader && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation()
                  navigate(`/?code=${leader.split('.')[0]}`)
                }}
                title="查看龙头股"
                className="block text-[11px] text-brand hover:underline"
              >
                龙头 {leader.split('.')[0]}
              </button>
            )}
            <div className={`text-sm font-medium ${
              item.score >= 80 ? 'text-emerald-400' : item.score >= 60 ? 'text-sky-400' : 'text-slate-400'
            }`}>
              {item.score.toFixed(0)}分
            </div>
          </div>
        </div>
      )
    })
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button
          onClick={() => setShowHistory(true)}
          className="rounded-lg border border-surface-700 bg-surface-850 px-3 py-1.5 text-xs text-slate-300 transition-colors hover:border-brand/50 hover:text-brand"
        >
          📅 近7日轮动记录
        </button>
      </div>

      {showHistory && (
        <RotationHistoryDialog
          concepts={concepts}
          industries={industries}
          onClose={() => setShowHistory(false)}
        />
      )}

      <div className="rounded-xl border border-surface-700 bg-surface-850 p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-medium text-slate-200">
            概念板块排名（{latestDate ?? '最新'}）
          </h2>
          <span className="text-[9px] text-slate-600">{stamp}</span>
        </div>
        <div className="space-y-2">{renderList(concepts, 'concept')}</div>
      </div>

      <div className="rounded-xl border border-surface-700 bg-surface-850 p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-medium text-slate-200">
            行业板块排名（{latestDate ?? '最新'}）
          </h2>
          <span className="text-[9px] text-slate-600">{stamp}</span>
        </div>
        <div className="space-y-2">{renderList(industries, 'industry')}</div>
      </div>
    </div>
  )
}

type Period = 'today' | '5d' | '10d'
type SortKey = 'main_net' | 'change_pct' | 'main_pct' | 'strength' | 'volume_ratio' | 'inflow_days'
type SortDir = 'asc' | 'desc'

const FLOW_PERIODS: { key: Period; label: string }[] = [
  { key: 'today', label: '今日' },
  { key: '5d', label: '近5日' },
  { key: '10d', label: '近10日' },
]
/** 10-08 决策：前端不出现数据源品牌字样，hint 展示前统一替换。 */
const BRAND_RE = /东财|开盘红|腾讯|同花顺|财联社/g

const flowPillCls = (active: boolean) =>
  `rounded-md px-3 py-1.5 text-sm transition-colors ${
    active ? 'bg-brand/20 text-brand' : 'text-slate-400 hover:text-slate-200'
  }`

/** 量比（无量纲，如 3.08），null → —。 */
const fmtRatio = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(2))
/** 板块强度（开盘啦内部指数，跨板块可比、跨口径慎比），null → —。 */
const fmtStrength = (v: number | null | undefined) => (v == null ? '—' : Math.round(v).toString())

function SectorFundFlow({ boardType, onBoardTypeChange, onSelectSector }: {
  boardType: BoardType
  onBoardTypeChange: (t: BoardType) => void
  onSelectSector: (s: { kind: string; name: string }) => void
}) {
  useMinuteTick()
  const [period, setPeriod] = useState<Period>('today')
  const [sortKey, setSortKey] = useState<SortKey>('main_net')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  // 自由选日期（2026-10-16）：null=今日实时；过去交易日=单日历史（仅主源能给，无东财降级）
  const [tradeDate, setTradeDate] = useState<string | null>(null)
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['boardFundFlow', boardType, period, tradeDate],
    queryFn: () => api.boardFundFlow(boardType, period, 50, tradeDate ?? undefined),
    staleTime: 60_000,
    retry: 1, // 主备切换已在服务层完成，前端少重试（防封）
    // 历史日期数据不变，不轮询；今日沿用盘中 3 分钟（放宽，防封）
    refetchInterval: tradeDate ? false : sessionRefreshMs(3 * 60_000),
  })

  // 信封嵌套：Envelope → data(DimensionResult) → data(真正 payload)
  const dim = (data?.data as any) ?? {}
  const payload = dim.data ?? {}
  const items: any[] = payload.item ?? []
  const degraded = payload.degraded === true
  const unavailable = dim.status === 'unavailable'
  const hint = (typeof payload.hint === 'string' ? payload.hint : '')
    .replace(BRAND_RE, '外部')

  // 多周期行 = 区间统计口径（区间涨幅/累计净额/区间净占比/净流入天数/区间强度），
  // 无量比/人气；单日（今日实时或历史某日）另有量比/人气列
  const isToday = period === 'today'
  const singleDay = isToday || !!tradeDate
  // 表头点击排序（2026-10-24 用户需求：撤排序 pill，列表头即排序器）：
  // 可排键集随视图变化（多周期无量比），失效键回退净流入降序
  const sortable: SortKey[] = singleDay
    ? ['main_net', 'change_pct', 'main_pct', 'strength', 'volume_ratio']
    : ['main_net', 'change_pct', 'main_pct', 'inflow_days', 'strength']
  const effSort: SortKey = sortable.includes(sortKey) ? sortKey : 'main_net'
  const toggleSort = (key: SortKey) => {
    if (key === effSort) {
      setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'))
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }
  const sorted = [...items].sort((a, b) => {
    const av = a[effSort]
    const bv = b[effSort]
    if (av == null) return 1 // 空值恒沉底，与升降序无关
    if (bv == null) return -1
    return sortDir === 'desc' ? bv - av : av - bv
  })

  const periodLabel = tradeDate
    ? `${tradeDate.slice(5)} 单日`
    : period === 'today'
      ? // 盘前/非交易日后端自动回落最近交易日，as_of 如实标注（无 as_of=真今日）
        (payload.as_of && payload.as_of !== localDateStr()
          ? `最近 ${String(payload.as_of).slice(5)}`
          : '今日')
      : `${FLOW_PERIODS.find((p) => p.key === period)?.label ?? ''}${
          typeof payload.as_of === 'string' && payload.as_of.includes('~')
            ? `（${payload.as_of}）`
            : ''
        }`
  // 表格头/行共用同一组列宽（今日 8 列，多周期 5 列）；
  // 名称列给 min-w 兜底——窄视口下固定列宽合计吃满容器，flex-1 会把名称挤成 0 宽
  const nameCell = 'min-w-[6rem] flex-1 truncate'
  // 表头即排序器：点列头按该列降序，再点切升序，箭头标方向
  const th = (key: SortKey, label: string, width: string) => (
    <button
      type="button"
      onClick={() => toggleSort(key)}
      title={`按${label}排序（再点切换升降序）`}
      className={`${width} whitespace-nowrap text-right transition-colors hover:text-slate-300 ${
        effSort === key ? 'font-medium text-brand' : 'text-slate-500'
      }`}
    >
      {label}
      {effSort === key ? (sortDir === 'desc' ? ' ↓' : ' ↑') : ''}
    </button>
  )
  const header = (
    <div className="flex items-center gap-2 border-b border-surface-700 px-3 pb-2 text-[11px]">
      <span className="w-6 text-center text-slate-500">#</span>
      <span className={`${nameCell} text-slate-500`}>板块</span>
      {th('change_pct', '涨跌', 'w-14')}
      {th('main_net', '净流入', 'w-16')}
      {th('main_pct', '净占比', 'w-14')}
      {singleDay ? (
        <>
          {th('strength', '强度', 'w-10')}
          {th('volume_ratio', '量比', 'w-10')}
          <span className="w-14 whitespace-nowrap text-right text-slate-500">人气</span>
        </>
      ) : (
        <>
          {th('inflow_days', '流入天数', 'w-12')}
          {th('strength', '强度', 'w-10')}
        </>
      )}
    </div>
  )

  return (
    <div className="space-y-4">
      {/* 板块 × 周期 两组 pill */}
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex gap-2">
          <button
            onClick={() => onBoardTypeChange('selected')}
            className={flowPillCls(boardType === 'selected')}
          >
            精选板块
          </button>
          <button
            onClick={() => onBoardTypeChange('industry')}
            className={flowPillCls(boardType === 'industry')}
          >
            行业板块
          </button>
        </div>
        <div className="flex gap-2">
          {FLOW_PERIODS.map((p) => (
            <button
              key={p.key}
              onClick={() => {
                setPeriod(p.key)
                setTradeDate(null) // 点周期 pill = 回到区间模式，清掉自选日期
              }}
              className={flowPillCls(!tradeDate && period === p.key)}
            >
              {p.label}
            </button>
          ))}
        </div>
        {/* 自由选日期：过去交易日=单日历史（主源独有能力）；今日=实时 */}
        <div className="flex items-center gap-2">
          <input
            type="date"
            min="2024-01-01"
            max={localDateStr()}
            value={tradeDate ?? ''}
            onChange={(e) => setTradeDate(e.target.value || null)}
            aria-label="选择历史日期查看单日资金流向"
            className="rounded-md border border-surface-700 bg-surface-850 px-2 py-1 text-xs text-slate-300 focus:border-brand/50 focus:outline-none"
          />
          {tradeDate && (
            <button
              onClick={() => setTradeDate(null)}
              className="rounded-md border border-surface-700 px-2 py-1 text-xs text-slate-400 transition-colors hover:border-brand/50 hover:text-brand"
            >
              回到今日
            </button>
          )}
        </div>
      </div>

      <div className="rounded-xl border border-surface-700 bg-surface-850 p-4">
        <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-sm font-medium text-slate-200">
            {boardType === 'selected' ? '精选' : '行业'}板块资金流向（{periodLabel}）
          </h2>
          <span className="text-[10px] text-slate-500">
            精选板块为外部精选概念池；行业板块与本地行业分类同源
          </span>
        </div>

        {isLoading ? (
          <div className="py-8 text-center text-sm text-slate-400">加载资金流向...</div>
        ) : unavailable ? (
          <div className="py-8 text-center">
            <div className="text-sm text-slate-400">
              资金流向数据暂时不可用
              {hint && <div className="mt-1 text-[11px] text-slate-500">{hint}</div>}
            </div>
            <button
              onClick={() => refetch()}
              className="mt-3 rounded-lg bg-surface-700 px-4 py-2 text-sm text-slate-300 hover:bg-surface-600"
            >
              重试
            </button>
          </div>
        ) : sorted.length === 0 ? (
          <div className="py-8 text-center text-sm text-slate-500">暂无数据</div>
        ) : (
          <>
            {degraded && (
              <div className="mb-2 text-[10px] text-slate-500">数据降级中，展示兜底口径</div>
            )}
            <div className="overflow-x-auto">
              <div className="min-w-[420px] space-y-1">
                {header}
                {sorted.map((item: any, i: number) => {
                  const mainNet = item.main_net ?? 0
                  const isPositive = mainNet >= 0
                  return (
                    <div
                      key={i}
                      onClick={() =>
                        onSelectSector({
                          kind: boardType === 'selected' ? 'concept' : 'industry',
                          name: item.name,
                        })
                      }
                      className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 transition-colors hover:bg-surface-800"
                    >
                      <span
                        className={`w-6 text-center text-sm font-bold ${
                          i < 3 ? 'text-amber-400' : 'text-slate-500'
                        }`}
                      >
                        {i + 1}
                      </span>
                      <span className={`${nameCell} text-sm text-slate-200`}>
                        {item.name}
                      </span>
                      <span className={`w-14 whitespace-nowrap text-right text-sm ${pctClass(item.change_pct)}`}>
                        {fmtIndexPct(item.change_pct)}
                      </span>
                      <span
                        className={`w-16 whitespace-nowrap text-right text-sm font-medium ${
                          isPositive ? 'text-rose-400' : 'text-emerald-400'
                        }`}
                      >
                        {(mainNet / 1e8).toFixed(2)}亿
                      </span>
                      <span className="w-14 whitespace-nowrap text-right text-sm text-slate-400">
                        {fmtIndexPct(item.main_pct)}
                      </span>
                      {singleDay ? (
                        <>
                          <span className="w-10 whitespace-nowrap text-right text-sm text-slate-300">
                            {fmtStrength(item.strength)}
                          </span>
                          <span className="w-10 whitespace-nowrap text-right text-sm text-slate-400">
                            {fmtRatio(item.volume_ratio)}
                          </span>
                          <span className="w-14 whitespace-nowrap text-right">
                            {item.popular_count != null && (
                              <span className="rounded border border-surface-600 px-1 py-px text-[9px] text-slate-400">
                                人气 {item.popular_count}
                              </span>
                            )}
                          </span>
                        </>
                      ) : (
                        <>
                          <span className="w-12 whitespace-nowrap text-right text-sm text-slate-400">
                            {item.inflow_days ?? '—'}
                          </span>
                          <span className="w-10 whitespace-nowrap text-right text-sm text-slate-300">
                            {fmtStrength(item.strength)}
                          </span>
                        </>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
