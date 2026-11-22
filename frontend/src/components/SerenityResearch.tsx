import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { SerenityJob, SerenityReport } from '../lib/types'
import { toast } from '../components/Toast'

const STRENGTH_CLS: Record<string, string> = {
  强: 'bg-emerald-500/15 text-emerald-300',
  中: 'bg-sky-500/15 text-sky-300',
  弱: 'bg-amber-500/15 text-amber-300',
  未证实: 'bg-slate-500/15 text-slate-400',
}

const BTN_CLS =
  'rounded-lg border border-brand/40 bg-brand/10 px-3 py-1 text-xs text-brand transition-colors hover:bg-brand/20 disabled:opacity-50'

function StrengthTag({ s }: { s: string }) {
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] ${STRENGTH_CLS[s] ?? STRENGTH_CLS['弱']}`}>
      {s}
    </span>
  )
}

/** 五维格与下方宽卡共用同一份研究任务的状态与启动入口。 */
export interface SerenityState {
  job?: SerenityJob
  result?: SerenityReport
  start: () => Promise<void>
  starting: boolean
}

/**
 * serenity 研究任务（一条任务两处展示）：
 * 五维网格里的「产业链研究」格与下方「产业链深度研究」大卡共享它，
 * 避免两处各自起一次研究、也因为共用 queryKey 而自动同步进度。
 */
export function useSerenityJob(code: string): SerenityState {
  const qc = useQueryClient()
  const [starting, setStarting] = useState(false)

  const query = useQuery({
    queryKey: ['serenityJob', code],
    queryFn: () => api.serenityStatus(code),
    enabled: !!code,
    staleTime: 3000,
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 3000 : false),
  })

  const start = async () => {
    setStarting(true)
    try {
      // 不传名称：由后端从数据源解析，避免把代码/旧名当成公司名喂给模型
      await api.serenityAnalyze(code)
      await qc.invalidateQueries({ queryKey: ['serenityJob', code] })
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e))
    } finally {
      setStarting(false)
    }
  }

  return { job: query.data, result: query.data?.result, start, starting }
}

/**
 * 五维网格里的紧凑体：状态 + 触发入口 +（完成后）一句话结论。
 * 完整报告在下方宽卡（宽卡才放得下卡点层级/证据/反方这些长内容）。
 */
export function SerenityDimensionBody({ job, result, start, starting }: SerenityState) {
  const status = job?.status ?? 'none'
  const running = status === 'running'

  return (
    <div className="space-y-2.5">
      {status === 'none' && (
        <p className="text-slate-400">
          这一维没有可聚合的数据服务——它是 AI 侧的产业链卡点研究，由下方研究器就地完成。
        </p>
      )}

      {running && (
        <p className="text-slate-400">正在跑产业链卡点研究（约 1 分钟）…</p>
      )}

      {status === 'failed' && (
        <p className="text-rose-300">研究失败：{job?.error}</p>
      )}

      {result && (
        <div className="space-y-2">
          {result.report?._partial && (
            <p className="text-amber-300/90 text-[12px]">模型输出被截断，结论不完整。</p>
          )}
          {result.report?.one_line && (
            <p className="text-slate-200">{result.report.one_line}</p>
          )}
          {result.report?.company_position?.role && (
            <p className="text-[12px] text-slate-400">
              产业链角色：
              <span className="ml-1 rounded bg-brand/15 px-1.5 py-0.5 text-[10px] text-brand">
                {result.report.company_position.role}
              </span>
            </p>
          )}
          <p className="text-[11px] text-slate-500">完整报告见下方「产业链深度研究」</p>
        </div>
      )}

      <button
        onClick={start}
        disabled={starting || running}
        className={BTN_CLS}
      >
        {running ? '研究中…' : result ? '重新研究' : '开始研究'}
      </button>
    </div>
  )
}

/**
 * 产业链深度研究（serenity-skill 方法论）：把该股的行情/估值/纪要/新闻 + serenity
 * "先排产业链层级/卡点，再排公司"的方法论喂给 LLM，产出卡点研究报告。
 * 快速扫描版（约 1 分钟），非 25 源全量深度调研。状态与入口由 useSerenityJob 提供。
 */
export default function SerenityResearch({ job, result, start, starting }: SerenityState) {
  const running = job?.status === 'running'

  return (
    <section className="rounded-xl border border-surface-700 bg-surface-850 p-4">
      <header className="mb-3 flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-slate-100">🔬 产业链深度研究</h3>
        <span className="text-[10px] text-slate-500">serenity-skill · 卡点猎手（LLM 快速扫描）</span>
        {running && (
          <span className="rounded bg-sky-500/15 px-2 py-0.5 text-[11px] text-sky-300">⟳ 研究中</span>
        )}
        {job?.status === 'failed' && (
          <span className="rounded bg-rose-500/15 px-2 py-0.5 text-[11px] text-rose-300">失败</span>
        )}
        <button
          onClick={start}
          disabled={starting || running}
          className={`ml-auto ${BTN_CLS}`}
        >
          {result ? '重新研究' : '开始深度研究'}
        </button>
      </header>

      {(!job || job.status === 'none') && (
        <p className="text-[12px] leading-relaxed text-slate-500">
          用 serenity-skill 的产业链瓶颈方法论，对该股做"先排产业链层级/卡点、再排公司"的深度研究
          （行情/估值/纪要/新闻 + LLM 推理）。<b className="text-slate-400">约 1 分钟，耗少量 LLM 额度</b>。
          点击右上角「开始深度研究」。
        </p>
      )}

      {running && (
        <p className="text-[12px] text-slate-400">正在跑产业链卡点研究（约 1 分钟）…</p>
      )}

      {job?.status === 'failed' && (
        <div className="rounded-md border border-rose-500/25 bg-rose-500/5 px-3 py-2 text-[12px] text-rose-300">
          研究失败：{job.error}
        </div>
      )}

      {result && <SerenityReportView result={result} />}
    </section>
  )
}

function SerenityReportView({ result }: { result: SerenityReport }) {
  const rep = result.report ?? {}
  return (
    <div className="space-y-3 text-[13px]">
      {rep._partial && (
        <div className="rounded-md border border-amber-500/25 bg-amber-500/5 px-3 py-2 text-[12px] text-amber-200/90">
          模型输出被截断，仅显示截断点之前的字段——建议重新研究。
        </div>
      )}

      {rep.one_line && (
        <div className="rounded-md border border-brand/30 bg-brand/5 px-3 py-2 leading-relaxed text-slate-100">
          {rep.one_line}
        </div>
      )}

      {rep.scarce_layers && rep.scarce_layers.length > 0 && (
        <div>
          <div className="mb-1.5 text-[11px] font-medium text-slate-400">卡点层级（先排层级，再排公司）</div>
          <div className="space-y-1.5">
            {rep.scarce_layers.map((l, i) => (
              <div key={i} className="flex items-start gap-2 rounded-md bg-surface-700/50 px-2.5 py-1.5 text-[12px]">
                <span className="w-24 shrink-0 font-medium text-slate-200">{l.layer}</span>
                <StrengthTag s={l.strength} />
                <span className="text-slate-400">{l.reason}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {rep.company_position && (
        <div className="rounded-md border border-surface-600 bg-surface-800 px-3 py-2 text-[12px] leading-relaxed">
          <span className="mr-2 rounded bg-brand/15 px-1.5 py-0.5 text-[10px] text-brand">
            {rep.company_position.role}
          </span>
          <span className="text-slate-400">
            处于 <b className="text-slate-300">{rep.company_position.layer}</b>：{rep.company_position.why}
          </span>
        </div>
      )}

      {rep.evidence && rep.evidence.length > 0 && (
        <div>
          <div className="mb-1.5 text-[11px] font-medium text-slate-400">证据（按强度分级）</div>
          <ul className="space-y-1">
            {rep.evidence.map((e, i) => (
              <li key={i} className="flex items-start gap-2 text-[12px] text-slate-400">
                <StrengthTag s={e.strength} />
                <span>{e.point}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {rep.market_missing && (
        <div className="rounded-md bg-surface-700/40 px-3 py-2 text-[12px] leading-relaxed text-slate-300">
          <b className="text-slate-200">市场可能没看清的地方：</b>
          {rep.market_missing}
        </div>
      )}

      {rep.bear_case && (
        <div className="rounded-md border border-amber-500/25 bg-amber-500/5 px-3 py-2 text-[12px] leading-relaxed text-amber-200/90">
          <b>反方理由（什么情况说明看错了）：</b>
          {rep.bear_case}
        </div>
      )}

      {rep.risks && rep.risks.length > 0 && (
        <div className="text-[12px] text-slate-400">
          <b className="text-slate-300">主要风险：</b>
          {rep.risks.join('；')}
        </div>
      )}

      {rep.next_checks && rep.next_checks.length > 0 && (
        <div className="text-[12px] text-slate-400">
          <b className="text-slate-300">下一步验证：</b>
          {rep.next_checks.join('；')}
        </div>
      )}

      {rep.raw && (
        <div className="whitespace-pre-wrap text-[12px] text-slate-400">{rep.raw}</div>
      )}

      <div className="text-[10px] text-slate-600">
        serenity 方法论快速扫描（模型 {result.model}）· 基于面板已有数据，非 25 源全量调研 ·
        该维为 AI 研究结论，不计入硬数据共振
      </div>
    </div>
  )
}
