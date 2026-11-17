/** 主线五分类公共判定（与看板 MainlineBoard 原逻辑一致，2026-10-14 抽出共用）。
 *  机械近似：rank1-2主线 / 3-4次主线 / 5+轮动 / 近5日仅1天=单日脉冲 /
 *  前日top5当日缺席且近5日≥3次=退潮。人工复盘以 AI 判定为准。 */
export type MainRow = {
  date: string
  member: string
  score: number
  rank: number
  limit_up_count: number
  max_boards: number
}

export type ClassifiedRow = MainRow & { cls: string; presence: number }

export const MAINLINE_BADGES: Record<string, { text: string; cls: string }> = {
  主线: { text: '主线', cls: 'border-rose-500/50 bg-rose-500/10 text-rose-300' },
  次主线: { text: '次主线', cls: 'border-amber-500/50 bg-amber-500/10 text-amber-300' },
  轮动: { text: '轮动', cls: 'border-sky-500/50 bg-sky-500/10 text-sky-300' },
  单日脉冲: { text: '单日脉冲', cls: 'border-violet-500/50 bg-violet-500/10 text-violet-300' },
  退潮: { text: '退潮', cls: 'border-slate-500/50 bg-slate-500/10 text-slate-400' },
}

/** 本地日期串（不能用 toISOString——UTC 在北京 0-8 点会差一天）。 */
export function localDateStr(d = new Date()): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** 对单日榜单（latest）做五分类并附近 N 日 presence。 */
export function classifyLatest(latest: MainRow[], allRows: MainRow[], recentDays = 5): ClassifiedRow[] {
  const dates = [...new Set(allRows.map((r) => r.date))].sort()
  const recent = dates.slice(-recentDays)
  const presenceOf = (member: string) =>
    recent.filter((d) => allRows.some((r) => r.date === d && r.member === member)).length
  return latest.map((r) => {
    const p = presenceOf(r.member)
    let cls: string
    if (p <= 1) cls = '单日脉冲'
    else if (r.rank <= 2) cls = '主线'
    else if (r.rank <= 4) cls = '次主线'
    else cls = '轮动'
    return { ...r, cls, presence: p }
  })
}

/** 前日 top5 当日缺席且近 N 日上榜 ≥3 次 → 退潮名单（最多 maxN 条）。 */
export function fadingMembers(allRows: MainRow[], maxN = 2): MainRow[] {
  const dates = [...new Set(allRows.map((r) => r.date))].sort()
  if (dates.length < 2) return []
  const latestDate = dates[dates.length - 1]
  const prevDate = dates[dates.length - 2]
  const latest = allRows.filter((r) => r.date === latestDate)
  const recent = dates.slice(-5)
  const presenceOf = (member: string) =>
    recent.filter((d) => allRows.some((r) => r.date === d && r.member === member)).length
  return allRows
    .filter((r) => r.date === prevDate && r.rank <= 5)
    .filter((r) => !latest.some((l) => l.member === r.member) && presenceOf(r.member) >= 3)
    .slice(0, maxN)
}
