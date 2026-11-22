import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Star } from 'lucide-react'
import { api } from '../../lib/api'
import { useAuth } from '../../auth'
import { useFlash } from './format'

/** 免费版关注横条：只显示自选股，不包含纪要股票池。 */
type WatchItem = { symbol: string; name?: string; group: string }

function QuoteChip({ symbol, name, quote, onClick }: {
  symbol: string
  name: string
  quote: any
  onClick: () => void
}) {
  const pct = quote?.price_change_ratio_pct
  const flash = useFlash(quote?.last_price)
  const pctCls = pct == null ? 'text-slate-400' : Number(pct) > 0 ? 'text-rose-400' : Number(pct) < 0 ? 'text-emerald-400' : 'text-slate-400'
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
    </button>
  )
}

export function WatchBar() {
  const navigate = useNavigate()
  const { isAdmin, can } = useAuth()
  const canWatch = isAdmin || can('watchlist')
  const wlQuery = useQuery({
    queryKey: ['watchlist-server'],
    queryFn: () => api.watchlist(),
    enabled: canWatch,
    staleTime: 30_000,
  })
  const watch = (wlQuery.data?.items ?? []) as WatchItem[]
  const groups = (wlQuery.data?.groups ?? []) as { id: string; name: string }[]
  const codes = [...new Set(watch.map((w) => w.symbol))]
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
  if (!canWatch || !watch.length) return null

  const groupName = (id: string) => groups.find((g) => g.id === id)?.name ?? '默认分组'
  const grouped: Record<string, WatchItem[]> = {}
  for (const w of watch) {
    ;(grouped[groupName(w.group)] ??= []).push(w)
  }
  const quoteAsOf = (quoteQuery.data as any)?.data?.as_of as string | undefined

  return (
    <div className="rounded-xl border border-surface-700 bg-surface-850 p-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-[11px] text-slate-400">我的关注 · 自选股</span>
        <span className="text-[9px] text-slate-600">{watch.length} 只</span>
        <span className="ml-auto text-[9px] text-slate-600">
          {quoteQuery.isFetching ? '刷新中…' : '15秒自动刷新'}
          {quoteAsOf ? ` · 报价截至 ${quoteAsOf}` : ''}
        </span>
      </div>
      <div className="space-y-1.5">
        {Object.entries(grouped).map(([gname, items]) => (
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
                onClick={() => navigate(`/?code=${w.symbol}`)}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}
