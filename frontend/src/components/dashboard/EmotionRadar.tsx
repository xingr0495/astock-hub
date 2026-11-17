import type { DashRadar } from "./types"
import { scoreColor } from "./format"

/** 六维情绪雷达（纯 SVG；颜色用字面量适配主题）。 */
export function EmotionRadar({ radar, score }: {
  radar: DashRadar[]
  score: number
}) {
  const size = 240
  const cx = size / 2
  const cy = size / 2
  const maxR = 78
  const color = scoreColor(score)

  if (!radar.length) {
    return <div className="flex h-52 items-center justify-center text-xs text-slate-400">暂无雷达数据</div>
  }

  const points = radar.map((r, i) => {
    const angle = -Math.PI / 2 + (i * 2 * Math.PI) / radar.length
    const radius = (maxR * Math.max(0, Math.min(100, r.value))) / 100
    return {
      ...r,
      x: cx + Math.cos(angle) * radius,
      y: cy + Math.sin(angle) * radius,
      lx: cx + Math.cos(angle) * (maxR + 27),
      ly: cy + Math.sin(angle) * (maxR + 27),
      gx: cx + Math.cos(angle) * maxR,
      gy: cy + Math.sin(angle) * maxR,
    }
  })
  const polygon = points.map((p) => `${p.x},${p.y}`).join(" ")
  const gridPolygons = [1, 0.66, 0.33].map((level, idx) => ({
    level,
    idx,
    points: radar.map((_, i) => {
      const angle = -Math.PI / 2 + (i * 2 * Math.PI) / radar.length
      return `${cx + Math.cos(angle) * maxR * level},${cy + Math.sin(angle) * maxR * level}`
    }).join(" "),
  }))

  return (
    <div className="flex justify-center">
      <svg viewBox={`0 0 ${size} ${size}`} className="h-56 w-full">
        <defs>
          <radialGradient id="emotionRadarFill" cx="50%" cy="45%" r="70%">
            <stop offset="0%" stopColor={`${color}57`} />
            <stop offset="100%" stopColor={`${color}1f`} />
          </radialGradient>
          <radialGradient id="emotionRadarCenter" cx="50%" cy="50%" r="55%">
            <stop offset="0%" stopColor="#16223a" stopOpacity="0.92" />
            <stop offset="68%" stopColor="#16223a" stopOpacity="0.7" />
            <stop offset="100%" stopColor="#16223a" stopOpacity="0" />
          </radialGradient>
        </defs>
        {gridPolygons.map((g) => (
          <polygon
            key={g.level}
            points={g.points}
            fill={g.idx % 2 === 0 ? "#16223a" : "#131d33"}
            stroke="#2a3a5f"
            strokeWidth={g.level === 1 ? 1.2 : 0.8}
          />
        ))}
        {points.map((p) => (
          <line key={p.key} x1={cx} y1={cy} x2={p.gx} y2={p.gy} stroke="#2a3a5f" strokeOpacity={0.6} />
        ))}
        <polygon points={polygon} fill="url(#emotionRadarFill)" stroke={color} strokeWidth={2} />
        {points.map((p) => (
          <circle key={p.key} cx={p.x} cy={p.y} r={2.8} fill={color} stroke="#16223a" strokeWidth={1} />
        ))}
        <circle cx={cx} cy={cy} r={29} fill="url(#emotionRadarCenter)" />
        <text x={cx} y={cy + 7} textAnchor="middle" fill="#f1f5f9" fontSize={24} fontWeight="bold" fontFamily="monospace">
          {score}
        </text>
        {points.map((p) => (
          <text key={`${p.key}-label`} x={p.lx} y={p.ly + 4} textAnchor="middle" fill="#cbd5e1" fontSize={10} fontWeight="medium">
            {p.label}
          </text>
        ))}
      </svg>
    </div>
  )
}
