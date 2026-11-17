/**
 * 财务报表组件 - 显示业绩预告、研报、估值数据
 */

import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { TrendingUp, FileText, BarChart3 } from 'lucide-react'

interface FinancialData {
  earnings: any[] | null
  reports: any[] | null
  valuation: any | null
  earnings_error?: string
  reports_error?: string
  valuation_error?: string
}

export function FinancialSection({ code, name }: { code: string; name?: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['financial', code],
    queryFn: () => api.financial(code),
    staleTime: 300_000, // 5分钟
    enabled: !!code,
  })

  if (isLoading) {
    return <div className="py-4 text-center text-xs text-slate-400">加载财务数据...</div>
  }

  if (error) {
    return <div className="py-4 text-center text-xs text-slate-500">财务数据加载失败</div>
  }

  const fin: FinancialData | null = data?.data || null
  if (!fin) {
    return <div className="py-4 text-center text-xs text-slate-500">暂无财务数据</div>
  }

  return (
    <div className="space-y-4">
      {/* 估值数据 */}
      {fin.valuation && (
        <div className="rounded-xl border border-surface-700 bg-surface-850 p-4">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-medium text-slate-200">
            <BarChart3 className="h-4 w-4 text-brand" />
            估值指标
          </h3>
          <div className="grid grid-cols-3 gap-3 text-sm">
            <div className="rounded-lg bg-surface-800 p-3">
              <div className="text-[11px] text-slate-500">PE(TTM)</div>
              <div className="text-lg font-bold text-slate-200">
                {fin.valuation.pe_ttm ? fin.valuation.pe_ttm.toFixed(2) : '-'}
              </div>
            </div>
            <div className="rounded-lg bg-surface-800 p-3">
              <div className="text-[11px] text-slate-500">PB</div>
              <div className="text-lg font-bold text-slate-200">
                {fin.valuation.pb_mrq ? fin.valuation.pb_mrq.toFixed(2) : '-'}
              </div>
            </div>
            <div className="rounded-lg bg-surface-800 p-3">
              <div className="text-[11px] text-slate-500">PS(TTM)</div>
              <div className="text-lg font-bold text-slate-200">
                {fin.valuation.ps_ttm ? fin.valuation.ps_ttm.toFixed(2) : '-'}
              </div>
            </div>
            <div className="rounded-lg bg-surface-800 p-3">
              <div className="text-[11px] text-slate-500">PE(动)</div>
              <div className="text-lg font-bold text-slate-200">
                {fin.valuation.pe_mrq ? fin.valuation.pe_mrq.toFixed(2) : '-'}
              </div>
            </div>
            <div className="rounded-lg bg-surface-800 p-3">
              <div className="text-[11px] text-slate-500">PCF(TTM)</div>
              <div className="text-lg font-bold text-slate-200">
                {fin.valuation.pcf_ttm ? fin.valuation.pcf_ttm.toFixed(2) : '-'}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 业绩预告 */}
      {fin.earnings && fin.earnings.length > 0 && (
        <div className="rounded-xl border border-surface-700 bg-surface-850 p-4">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-medium text-slate-200">
            <TrendingUp className="h-4 w-4 text-emerald-400" />
            业绩预告
          </h3>
          <div className="space-y-2 max-h-[200px] overflow-y-auto">
            {fin.earnings.slice(0, 5).map((item, i) => (
              <div key={i} className="rounded-lg bg-surface-800 px-3 py-2">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-slate-200">
                    {item.report_date} {item.indicator}
                  </span>
                  <span className={`rounded px-2 py-0.5 text-xs ${
                    item.forecast_type?.includes('增') ? 'bg-emerald-500/20 text-emerald-300' :
                    item.forecast_type?.includes('减') ? 'bg-rose-500/20 text-rose-300' :
                    'bg-surface-700 text-slate-400'
                  }`}>
                    {item.forecast_type}
                  </span>
                </div>
                <div className="mt-1 text-[11px] text-slate-500">
                  变动: {item.change_pct_lower ? `${item.change_pct_lower.toFixed(2)}%` : '-'}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 研报 */}
      {fin.reports && fin.reports.length > 0 && (
        <div className="rounded-xl border border-surface-700 bg-surface-850 p-4">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-medium text-slate-200">
            <FileText className="h-4 w-4 text-sky-400" />
            机构研报
          </h3>
          <div className="space-y-2 max-h-[300px] overflow-y-auto">
            {fin.reports.slice(0, 5).map((item, i) => (
              <div key={i} className="rounded-lg bg-surface-800 px-3 py-2">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="text-sm text-slate-200 line-clamp-2">
                      {item.title}
                    </div>
                    <div className="mt-1 text-[11px] text-slate-500">
                      {item.publishDate} · {item.orgSName}
                    </div>
                  </div>
                  <span className={`ml-2 rounded px-2 py-0.5 text-xs whitespace-nowrap ${
                    item.emRatingName === '买入' ? 'bg-emerald-500/20 text-emerald-300' :
                    item.emRatingName === '增持' ? 'bg-sky-500/20 text-sky-300' :
                    item.emRatingName === '减持' || item.emRatingName === '卖出' ? 'bg-rose-500/20 text-rose-300' :
                    'bg-surface-700 text-slate-400'
                  }`}>
                    {item.emRatingName}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 无数据提示 */}
      {!fin.earnings?.length && !fin.reports?.length && !fin.valuation && (
        <div className="py-4 text-center text-xs text-slate-500">
          暂无财务数据
        </div>
      )}
    </div>
  )
}
