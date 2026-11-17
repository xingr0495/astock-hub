import { useState } from 'react'
import { Modal } from './Modal'

type Row = {
  date: string
  member: string
  score: number
  rank: number
  limit_up_count: number
  max_boards: number
}

interface Props {
  concepts: Row[]
  industries: Row[]
  onClose: () => void
}

const DAY_TOP = 8 // 每日取前 N 名进入矩阵（控制宽度与噪音）

/**
 * 近 7 个交易日轮动记录矩阵：概念/行业切换 × 日期列。
 * 单元格 = 当日排名（1-2 金 / 3-5 蓝 / 其余灰，未上榜 = ·），
 * 末列 = 上榜天数。一眼看出谁在进场（低→高）、谁在退潮（高→缺）。
 */
export default function RotationHistoryDialog({ concepts, industries, onClose }: Props) {
  const [kind, setKind] = useState<'concept' | 'industry'>('concept')
  const rows = kind === 'concept' ? concepts : industries

  const dates = [...new Set(rows.map((r) => String(r.date)))].sort().slice(-7)
  const dayRows = dates.map((d) =>
    rows.filter((r) => String(r.date) === d).sort((a, b) => a.rank - b.rank),
  )

  // 每日 TOP 集合并集；排名矩阵
  const rankOf = new Map<string, Map<string, number>>()
  const names = new Set<string>()
  dayRows.forEach((list, di) => {
    list.slice(0, DAY_TOP).forEach((r) => {
      names.add(r.member)
      if (!rankOf.has(r.member)) rankOf.set(r.member, new Map())
      rankOf.get(r.member)!.set(dates[di], r.rank)
    })
  })

  const latestDate = dates[dates.length - 1]
  const ordered = [...names].sort((a, b) => {
    const ra = rankOf.get(a)!.get(latestDate) ?? 99
    const rb = rankOf.get(b)!.get(latestDate) ?? 99
    if (ra !== rb) return ra - rb
    const ma = Math.min(...[...rankOf.get(a)!.values()])
    const mb = Math.min(...[...rankOf.get(b)!.values()])
    return ma - mb
  })

  const cellCls = (r: number | undefined) =>
    r === undefined
      ? 'text-slate-700'
      : r <= 2
        ? 'font-semibold text-amber-400'
        : r <= 5
          ? 'text-sky-400'
          : 'text-slate-400'

  return (
    <Modal
      onClose={onClose}
      labelledBy="rotation-history-title"
      panelClassName="w-[94vw] max-w-3xl rounded-xl border border-surface-700 bg-surface-850 shadow-2xl"
    >
      <header className="flex items-center gap-3 border-b border-surface-700 p-4">
        <h2 id="rotation-history-title" className="text-sm font-semibold text-slate-100">
          近 7 日轮动记录
        </h2>
        <div className="flex gap-1 rounded-lg bg-surface-800 p-0.5">
          {(['concept', 'industry'] as const).map((k) => (
            <button
              key={k}
              onClick={() => setKind(k)}
              className={`rounded px-2 py-0.5 text-[11px] transition-colors ${
                kind === k ? 'bg-brand/20 text-brand' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {k === 'concept' ? '概念' : '行业'}
            </button>
          ))}
        </div>
        <button
          onClick={onClose}
          className="ml-auto rounded-md px-2 py-1 text-slate-400 hover:bg-surface-700 hover:text-slate-200"
          aria-label="关闭"
        >
          ✕
        </button>
      </header>

      <div className="max-h-[70vh] overflow-auto p-4">
        {ordered.length === 0 ? (
          <p className="text-[12px] text-slate-500">暂无轮动记录数据。</p>
        ) : (
          <table className="w-full border-collapse text-[11px]">
            <thead>
              <tr className="text-slate-500">
                <th className="sticky left-0 bg-surface-850 px-2 py-1.5 text-left font-normal">
                  板块
                </th>
                {dates.map((d) => (
                  <th key={d} className="px-1.5 py-1.5 text-center font-normal tabular-nums">
                    {d.slice(5)}
                  </th>
                ))}
                <th className="px-2 py-1.5 text-center text-slate-600">上榜</th>
              </tr>
            </thead>
            <tbody>
              {ordered.map((name) => {
                const m = rankOf.get(name)!
                const n = dates.filter((d) => m.has(d)).length
                return (
                  <tr key={name} className="border-t border-surface-700/50">
                    <td className="sticky left-0 max-w-36 truncate bg-surface-850 px-2 py-1.5 text-slate-200">
                      {name}
                    </td>
                    {dates.map((d) => {
                      const r = m.get(d)
                      return (
                        <td key={d} className={`px-1.5 py-1.5 text-center tabular-nums ${cellCls(r)}`}>
                          {r ?? '·'}
                        </td>
                      )
                    })}
                    <td className="px-2 py-1.5 text-center text-slate-600">{n}/{dates.length}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
        <p className="mt-3 text-[10px] text-slate-600">
          每日取前 {DAY_TOP} 名入矩阵；金=前2名，蓝=3-5名，·=未上榜。
          读法：数字由大变小/由·变小 = 进场，由小变大/消失 = 退潮。
        </p>
      </div>
    </Modal>
  )
}
