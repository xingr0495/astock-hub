import type { ReactNode } from 'react'
import type { DimensionResult } from '../lib/types'
import StatusBadge from './StatusBadge'

/**
 * 五维卡片：统一展示「维度名 + 状态徽标 + 数据时间 + 来源 + 数据正文」。
 * 数据正文由 pages 通过 children 传入，天然保留各维度的独有结构。
 * 维度本身没有数据状态、而是由页面侧任务决定状态时（如 serenity 研究），
 * 用 badgeStatus 覆盖徽标文案。
 */
export default function DimensionCard({
  icon,
  title,
  dim,
  badgeStatus,
  className = '',
  children,
}: {
  icon: string
  title: string
  dim: DimensionResult | undefined
  badgeStatus?: string
  className?: string
  children: ReactNode
}) {
  const meta = dim?.meta
  return (
    <section
      data-dim-card=""
      className={`flex h-full flex-col rounded-xl border border-surface-700 bg-surface-850 p-4 ${className}`}
    >
      <header className="mb-3 flex flex-shrink-0 flex-wrap items-center gap-2">
        <span className="text-base leading-none">{icon}</span>
        <h3 className="text-sm font-semibold text-slate-100">{title}</h3>
        {dim && <StatusBadge status={badgeStatus ?? dim.status} dim={badgeStatus ? undefined : dim} />}
        <div className="ml-auto text-right">
          {meta?.as_of && (
            <div className="text-[11px] text-slate-500">数据时间 {meta.as_of}</div>
          )}
        </div>
      </header>

      {/* 等高：正文区 flex-1 填满卡片高度；信息面等长内容超出时内部滚动，
          保证同一行卡片高度一致而不是把整张卡片撑长 */}
      <div className="min-h-0 flex-1 overflow-y-auto pr-1 text-[13px] leading-relaxed text-slate-300">
        {children}
      </div>

      {dim?.error && (
        <div className="mt-3 rounded-md border border-rose-500/20 bg-rose-500/5 px-3 py-2 text-[12px] text-rose-300">
          <div className="font-medium">原因：{dim.error.message}</div>
          {dim.error.hint && <div className="mt-1 text-rose-200/80">→ {dim.error.hint}</div>}
        </div>
      )}
    </section>
  )
}