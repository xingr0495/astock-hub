import type { DimensionResult } from '../lib/types'

const STATUS_META: Record<
  string,
  { label: string; cls: string; note: string }
> = {
  ok: { label: '正常', cls: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30', note: '' },
  stale: { label: '数据偏旧', cls: 'bg-amber-500/15 text-amber-400 border-amber-500/30', note: '陈旧数据，不计入共振' },
  partial: { label: '部分可用', cls: 'bg-sky-500/15 text-sky-400 border-sky-500/30', note: '部分数据缺失' },
  not_found: { label: '无数据', cls: 'bg-slate-500/15 text-slate-400 border-slate-500/30', note: '未覆盖 ≠ 看空' },
  unavailable: { label: '不可用', cls: 'bg-rose-500/15 text-rose-400 border-rose-500/30', note: '数据缺失，不计入共振' },
  skipped: { label: '已停用', cls: 'bg-violet-500/15 text-violet-400 border-violet-500/30', note: '该维度已停用' },
  not_applicable: { label: '不适用', cls: 'bg-slate-500/15 text-slate-500 border-slate-600/40', note: '' },

  // serenity 研究任务状态（来自 /api/serenity/analyze，非数据维度状态）
  none: { label: '未运行', cls: 'bg-slate-500/15 text-slate-400 border-slate-500/30', note: '' },
  running: { label: '研究中', cls: 'bg-sky-500/15 text-sky-400 border-sky-500/30', note: '' },
  done: { label: '已完成', cls: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30', note: 'AI 研究结论，不计入共振' },
  failed: { label: '失败', cls: 'bg-rose-500/15 text-rose-400 border-rose-500/30', note: '' },
}

export default function StatusBadge({
  status,
  dim,
}: {
  status: string
  dim?: DimensionResult | null
}) {
  const meta = STATUS_META[status] ?? {
    label: status,
    cls: 'bg-slate-500/15 text-slate-400 border-slate-500/30',
    note: '',
  }

  const hint = dim?.error?.hint
  const title = hint ? `⚠️ ${dim?.error?.message ?? ''}\n${hint}` : dim?.error?.message

  return (
    <span
      title={title || meta.note || undefined}
      className={`inline-flex cursor-default items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] leading-none ${meta.cls}`}
    >
      {meta.label}
      {meta.note && <span className="hidden text-[10px] opacity-70 lg:inline">({meta.note})</span>}
    </span>
  )
}