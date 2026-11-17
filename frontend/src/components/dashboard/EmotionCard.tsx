import type { DashRadar } from './types'
import { scoreColor } from './format'

/**
 * 情绪主卡（第3期视觉打磨）：大数字 + 阶段药丸 + 六维横条。
 * 替代六维雷达——雷达看形状但结论不突出，这里把"多少分、什么阶段、
 * 哪个维度拖后腿"一眼说完。
 */
export function EmotionCard({ radar, score, label }: {
  radar: DashRadar[]
  score: number
  label: string
}) {
  const color = scoreColor(score)
  const max = Math.max(...radar.map((r) => r.value), 1)

  if (!radar.length) {
    return <div className="flex h-56 items-center justify-center text-xs text-slate-400">暂无情绪数据</div>
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-end justify-between">
        <div className="flex items-baseline gap-2">
          <span
            className="font-mono text-5xl font-bold leading-none tabular-nums"
            style={{ color }}
          >
            {score}
          </span>
          <span className="text-xs text-slate-500">/100</span>
        </div>
        <span
          className="rounded-full border px-2.5 py-1 text-xs font-semibold"
          style={{ color, borderColor: `${color}66`, background: `${color}1a` }}
        >
          {label}
        </span>
      </div>

      {/* 六维横条 */}
      <div className="mt-4 flex-1 space-y-2">
        {radar.map((r) => {
          const v = Math.max(0, Math.min(100, r.value))
          const c = scoreColor(v)
          return (
            <div key={r.key} className="flex items-center gap-2">
              <span className="w-14 shrink-0 text-[10px] text-slate-400">{r.label}</span>
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-800">
                <div
                  className="h-full rounded-full transition-all duration-700"
                  style={{ width: `${v}%`, background: c }}
                />
              </div>
              <span
                className="w-8 shrink-0 text-right font-mono text-[10px] tabular-nums"
                style={{ color: c }}
              >
                {v}
              </span>
            </div>
          )
        })}
      </div>

      {/* 视觉锚：与均值对比的提示行 */}
      <div className="mt-3 border-t border-surface-700 pt-2 text-[10px] text-slate-500">
        维度最高：<span style={{ color: scoreColor(max) }}>{radar.find((r) => r.value === max)?.label}</span>
        {' · '}维度均值 {Math.round(radar.reduce((s, r) => s + r.value, 0) / radar.length)}
      </div>
    </div>
  )
}
