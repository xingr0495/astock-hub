import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Star, Sparkles } from 'lucide-react'
import { api } from '../../lib/api'
import { useAuth } from '../../auth'
import { useFlash } from './format'

/**
 * 我的关注横条（D·第2期）：自选股（按分组）+ 纪要股票池（提及≥3天）实时行情。
 * 点击任意标的跳个股五维分析。
 */
type WatchItem = { symbol: string; name?: string; group: string }
type PoolStock = { symbol: string; name: string; count: number; last_seen: string }

function QuoteChip({
  symbol, name, count, quote, onClick,
}: {
  symbol: string
  name: string
  count?: number
  quote: any
  onClick: () => void
}) {
  const pct = quote?.price_change_ratio_pct
  const flash = useFlash(quote?.last_price)
  const pctCls =
    pct == null ? 'text-slate-400' : Number(pct) > 0 ? 'text-rose-400' : Number(pct) < 0 ? 'text-emerald-400' : 'text-slate-400'
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-1.5 rounded-md border border-surface-700 bg-surface-800/60 px-2 py-1.5 text-left hover:border-brand/40"
    >
      <span className="truncate text-[11px] text-slate-200">{name || symbol}</span>
      <span className={`px-0.5 text-[11px] font-semibold ${pctCls} ${flash}`}>
        {quote?.last_price != null ? Number(quote.last_price).toFixed(2) : '--'}
      </span>
      <span className={`text-[10px] ${pctCls}`}>
        {pct != null ? `${Number(pct) >= 0 ? '+' : ''}${Number(pct).toFixed(2)}%` : ''}
      </span>
      {count != null && (
        <span className="rounded bg-brand/20 px-1 text-[9px] font-semibold text-brand">{count}天</span>
      )}
    </button>
  )
}

export function WatchBar() {
  const navigate = useNavigate()
  const { isAdmin, can } = useAuth()
  // 权限闸（任务 11）：无权限不发挂载即查的请求，免 403；admin 恒真
  const canWatch = isAdmin || can('watchlist')
  const canPool = isAdmin || can('zsxq')

  const wlQuery = useQuery({
    queryKey: ['watchlist-server'],
    queryFn: () => api.watchlist(),
    enabled: canWatch,
    staleTime: 30_000,
  })
  const poolQuery = useQuery({
    queryKey: ['digest-pool'],
    queryFn: () => api.digestPool(),
    enabled: canPool,
    staleTime: 60_000,
  })

  const watch = (wlQuery.data?.items ?? []) as WatchItem[]
  const groups = (wlQuery.data?.groups ?? []) as { id: string; name: string }[]
  const poolTop = ((poolQuery.data?.stocks ?? []) as PoolStock[])
    .filter((s) => s.count >= 3)
    .slice(0, 8)

  const codes = [...new Set([...watch.map((w) => w.symbol), ...poolTop.map((s) => s.symbol)])]
  const quoteQuery = useQuery({
    queryKey: ['watchbar-quotes', codes.join(',')],
    queryFn: () => api.quote(codes.join(',')),
    enabled: codes.length > 0,
    staleTime: 8_000,
    refetchInterval: 15_000,
  })
  const quoteMap: Record<string, any> = {}
  for (const it of ((quoteQuery.data as any)?.data?.item ?? [])) {
    const code = (it.symbol || '').split('.')[0]
    if (code) quoteMap[code] = it
  }

  if (!canWatch && !canPool) return null
  if (!watch.length && !poolTop.length) return null

  const groupName = (id: string) => groups.find((g) => g.id === id)?.name ?? '默认分组'
  const grouped: Record<string, WatchItem[]> = {}
  for (const w of watch) {
    ;(grouped[groupName(w.group)] ??= []).push(w)
  }
  const go = (symbol: string) => navigate(`/?code=${symbol}`)
  const quoteAsOf = (quoteQuery.data as any)?.data?.as_of as string | undefined

  return (
    <div className="rounded-xl border border-surface-700 bg-surface-850 p-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-[11px] text-slate-400">我的关注 · 自选股 + 纪要股票池（≥3天）</span>
        <span className="text-[9px] text-slate-600">
          {canWatch && `${watch.length} 自选`}
          {canWatch && canPool && ' · '}
          {canPool && `${poolTop.length} 池`}
        </span>
        <span className="ml-auto text-[9px] text-slate-600">
          {quoteQuery.isFetching ? '刷新中…' : '15秒自动刷新'}
          {quoteAsOf ? ` · 报价截至 ${quoteAsOf}` : ''}
        </span>
      </div>

      <div className={canWatch && canPool ? 'grid grid-cols-1 gap-3 lg:grid-cols-2' : 'gap-3'}>
        {/* 自选股（按分组） */}
        {canWatch && (
        <div className="space-y-1.5">
          {watch.length === 0 ? (
            <div className="rounded-md border border-dashed border-surface-600 px-3 py-2 text-[10px] text-slate-500">
              暂无自选股 — 到「自选股」页添加
            </div>
          ) : (
            Object.entries(grouped).map(([gname, items]) => (
              <div key={gname} className="flex flex-wrap items-center gap-1.5">
                <span className="w-20 shrink-0 truncate text-[10px] text-slate-500" title={gname}>
                  <Star className="mr-1 inline h-3 w-3 text-amber-400" />
                  {gname}
                </span>
                {items.map((w) => (
                  <QuoteChip
                    key={w.symbol}
                    symbol={w.symbol}
                    name={w.name || quoteMap[w.symbol]?.name || ''}
                    quote={quoteMap[w.symbol]}
                    onClick={() => go(w.symbol)}
                  />
                ))}
              </div>
            ))
          )}
        </div>
        )}

        {/* 纪要股票池（提及≥3天） */}
        {canPool && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="w-20 shrink-0 text-[10px] text-slate-500">
            <Sparkles className="mr-1 inline h-3 w-3 text-brand" />
            池·高频
          </span>
          {poolTop.length === 0 ? (
            <span className="text-[10px] text-slate-500">暂无 ≥3 天提及标的</span>
          ) : (
            poolTop.map((s) => (
              <QuoteChip
                key={s.symbol}
                symbol={s.symbol}
                name={s.name}
                count={s.count}
                quote={quoteMap[s.symbol]}
                onClick={() => go(s.symbol)}
              />
            ))
          )}
        </div>
        )}
      </div>
    </div>
  )
}
