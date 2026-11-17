import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { TA_REPORT_LABELS, type TaResult } from '../lib/types'

const DECISION_META: Record<string, { label: string; cls: string }> = {
  buy: { label: 'BUY 买入', cls: 'bg-rose-500/15 text-rose-300 border-rose-500/40' },
  hold: { label: 'HOLD 观望', cls: 'bg-amber-500/15 text-amber-300 border-amber-500/40' },
  sell: { label: 'SELL 卖出', cls: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40' },
}

function decisionMeta(decision: string) {
  const d = (decision || '').toLowerCase()
  if (d.includes('sell') || d.includes('减持')) return DECISION_META.sell
  if (d.includes('buy') || d.includes('增持')) return DECISION_META.buy
  return DECISION_META.hold
}

/**
 * 深度多 Agent 分析（TradingAgents-astock）：7 个分析师角色 + 多空/风险辩论。
 * 长任务（约 20 分钟 + LLM 成本），点击按钮触发后台任务并轮询结果。
 */
export default function TaDeepAnalysis({ code }: { code: string }) {
  const [kick, setKick] = useState(0)
  const [starting, setStarting] = useState(false)

  const status = useQuery({
    queryKey: ['taJob', code, kick],
    queryFn: () => api.taAnalyzeStatus(code),
    enabled: !!code,
    staleTime: 3000,
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 5000 : false),
  })

  const job = status.data
  const result = job?.result

  const start = async () => {
    setStarting(true)
    try {
      await api.taAnalyze(code)
      setKick((k) => k + 1)
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e))
    } finally {
      setStarting(false)
    }
  }

  const meta = result ? decisionMeta(result.decision) : null

  return (
    <section className="rounded-xl border border-surface-700 bg-surface-850 p-4">
      <header className="mb-3 flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-slate-100">🔬 深度多Agent分析</h3>
        <span className="text-[10px] text-slate-500">TradingAgents-astock · 7 分析师 + 多空/风险辩论</span>
        {job?.status === 'running' && (
          <span className="rounded bg-sky-500/15 px-2 py-0.5 text-[11px] text-sky-300">⟳ 分析中</span>
        )}
        {job?.status === 'failed' && (
          <span className="rounded bg-rose-500/15 px-2 py-0.5 text-[11px] text-rose-300">失败</span>
        )}
        {meta && (
          <span className={`rounded border px-2 py-0.5 text-[11px] font-medium ${meta.cls}`}>
            {meta.label}
          </span>
        )}
        {job?.llm_channel === 'api-fallback' && (
          <span className="text-[10px] text-slate-500">
            订阅通道不支持 TA，本次使用 API 通道（{job.llm_model || '配置档模型'}）
          </span>
        )}
        <button
          onClick={start}
          disabled={starting || job?.status === 'running'}
          className="ml-auto rounded-lg border border-brand/40 bg-brand/10 px-3 py-1 text-xs text-brand transition-colors hover:bg-brand/20 disabled:opacity-50"
        >
          {result ? '重新分析' : '开始深度分析'}
        </button>
      </header>

      {(!job || job.status === 'none') && (
        <p className="text-[12px] leading-relaxed text-slate-500">
          由 TradingAgents-astock 的多 Agent 框架对该股做深度投研：市场/情绪/新闻/基本面/政策/游资/解禁
          七个分析师 + 多空辩论 + 三方风险辩论。<b className="text-slate-400">耗时约 20 分钟，会消耗 LLM 额度</b>。
          点击右上角「开始深度分析」触发。
        </p>
      )}

      {job?.status === 'running' && (
        <p className="text-[12px] text-slate-400">
          正在做深度多 Agent 分析（分析日期 {job.date}，于 {job.started_at?.slice(11, 16)} 启动）…
          <span className="text-slate-500">通常 15–25 分钟，可先去别的页面，回来再看。</span>
        </p>
      )}

      {job?.status === 'failed' && (
        <div className="rounded-md border border-rose-500/25 bg-rose-500/5 px-3 py-2 text-[12px] text-rose-300">
          分析失败：{job.error}
        </div>
      )}

      {result && <TaResultView result={result} date={job?.date} />}
    </section>
  )
}

function TaResultView({ result, date }: { result: TaResult; date?: string }) {
  const [open, setOpen] = useState<string | null>('fundamentals_report')
  return (
    <div className="space-y-3">
      <div className="rounded-md border border-surface-600 bg-surface-800 px-3 py-2 text-[13px] leading-relaxed text-slate-200">
        <span className="mr-2 text-[10px] text-slate-500">分析日期 {result.date ?? date}</span>
        {result.final_trade_decision}
      </div>

      {Object.entries(TA_REPORT_LABELS).map(([key, label]) => {
        const text = result.reports?.[key]
        if (!text) return null
        const isOpen = open === key
        return (
          <div key={key} className="rounded-md border border-surface-700/80 bg-surface-800/60">
            <button
              onClick={() => setOpen(isOpen ? null : key)}
              className="flex w-full items-center justify-between px-3 py-2 text-left text-[12px] font-medium text-slate-200"
            >
              <span>{label}</span>
              <span className="text-slate-500">{isOpen ? '−' : '+'}</span>
            </button>
            {isOpen && (
              <div className="border-t border-surface-700/60 px-3 py-2 text-[12px] leading-relaxed text-slate-400 whitespace-pre-wrap">
                {text}
              </div>
            )}
          </div>
        )
      })}

      {result.debate_judge && (
        <details className="rounded-md border border-surface-700/80 bg-surface-800/60 px-3 py-2 text-[12px] text-slate-400">
          <summary className="cursor-pointer font-medium text-slate-200">多空辩论裁决</summary>
          <div className="mt-2 whitespace-pre-wrap leading-relaxed">{result.debate_judge}</div>
        </details>
      )}
      {result.risk_judge && (
        <details className="rounded-md border border-surface-700/80 bg-surface-800/60 px-3 py-2 text-[12px] text-slate-400">
          <summary className="cursor-pointer font-medium text-slate-200">风险辩论裁决</summary>
          <div className="mt-2 whitespace-pre-wrap leading-relaxed">{result.risk_judge}</div>
        </details>
      )}
    </div>
  )
}