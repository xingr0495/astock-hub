import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Modal } from './Modal'
import { api } from '../lib/api'
import KlineChart from './KlineChart'

interface Props {
  /** 6 位代码（如 002837） */
  code: string
  onClose: () => void
}

/**
 * 跨页股票预览弹窗：不离开当前榜单，快速看某只票的核心快照 + 近期K线。
 * 数据复用 /api/bundle 与 /api/kline（与个股页同源）。
 */
export default function StockPreviewDialog({ code, onClose }: Props) {
  const query = useQuery({
    queryKey: ['preview', code],
    queryFn: () =>
      Promise.all([api.bundle(code), api.kline(code, 60)]),
    enabled: !!code,
  })

  const [bundle, kline] = query.data ?? [undefined, undefined]

  const name = bundle?.query?.name ?? code
  const tsCode = (bundle?.query?.ts_code as string) ?? code

  // 最新价/涨跌：用 kline 末根K线（与页面同源，简单可靠）
  const kRows = ((kline?.data?.rows ?? []) as Record<string, any>[])
  const last = kRows[kRows.length - 1] ?? null

  // 维度状态概览
  const dims = (bundle?.dimensions ?? {}) as Record<string, any>
  const dimLine: [string, string][] = Object.entries(dims).map(
    ([k, v]) => [k, v?.status ?? '—'],
  )

  return (
    <Modal
      onClose={onClose}
      labelledBy="preview-title"
      panelClassName="w-[92vw] max-w-2xl rounded-xl border border-surface-700 bg-surface-850 shadow-2xl"
    >
      <header className="flex items-start gap-3 border-b border-surface-700 p-4">
        <div className="flex-1">
          <h2 id="preview-title" className="text-base font-semibold text-slate-100">
            {name}
            <span className="ml-2 text-xs font-normal text-slate-500">{tsCode}</span>
          </h2>
          {last && (
            <div className="mt-1 flex items-center gap-3 text-sm">
              <span className="text-xl font-semibold text-slate-100">
                {Number(last.close).toFixed(2)}
              </span>
              {last.change_pct != null && (
                <span
                  className={
                    Number(last.change_pct) >= 0 ? 'text-rose-400' : 'text-emerald-400'}
                >
                  {Number(last.change_pct) >= 0 ? '+' : ''}
                  {(Number(last.change_pct) * 100).toFixed(2)}%
                </span>
              )}
            </div>
          )}
        </div>
        <button
          onClick={onClose}
          className="rounded-md px-2 py-1 text-slate-400 hover:bg-surface-700 hover:text-slate-200"
          aria-label="关闭"
        >
          ✕
        </button>
      </header>

      <div className="space-y-4 p-4">
        {query.isLoading && (
          <div className="py-10 text-center text-xs text-slate-400">正在加载预览…</div>
        )}

        {query.isError && (
          <div className="py-8 text-center text-xs text-rose-300">
            加载失败：{query.error instanceof Error ? query.error.message : ''}
          </div>
        )}

        {query.data && (
          <>
            {/* 五维状态 */}
            <div className="flex flex-wrap gap-1.5">
              {dimLine.map(([k, status]) => (
                <span
                  key={k}
                  className="rounded bg-surface-700/60 px-2 py-0.5 text-[10px] text-slate-300"
                >
                  {k}：{status}
                </span>
              ))}
            </div>

            {/* 近 60 日K线 */}
            <div className="h-56 rounded-lg border border-surface-700 p-2">
              <KlineChart rows={((kline?.data?.rows ?? []) as any[])} height={208} />
            </div>

            <div className="flex justify-end">
              <Link
                to={`/?code=${code}`}
                onClick={onClose}
                className="rounded-lg bg-brand/15 px-3 py-1.5 text-xs text-brand hover:bg-brand/25"
              >
                打开完整五维分析 →
              </Link>
            </div>
          </>
        )}
      </div>
    </Modal>
  )
}
