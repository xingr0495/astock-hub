interface Props {
  values: number[]
  width?: number
  height?: number
  color?: string
}

/**
 * 轻量迷你走势图（SVG，无第三方依赖）：用于表格行内快速看近期趋势。
 * 数据为空时返回占位。端点连线、按组件内 min/max 缩放到高度。
 */
export default function Sparkline({
  values,
  width = 80,
  height = 24,
  color = '#38bdf8',
}: Props) {
  if (!values || values.length < 2) {
    return (
      <svg width={width} height={height}>
        <line x1="0" y1={height / 2} x2={width} y2={height / 2}
          stroke="#334155" strokeDasharray="2 3" strokeWidth="1" />
      </svg>
    )
  }

  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min
  const pad = 2
  const stepX = (width - pad * 2) / (values.length - 1)

  const points = values.map((v, i) => {
    const x = pad + i * stepX
    const y =
      range > 0
        ? pad + (height - pad * 2) * (1 - (v - min) / range)
        : height / 2
    return [x, y] as const
  })

  // 末点比首点高 → 涨色（红），低 → 跌色（绿），平 → 默认色
  const trendColor =
    values[values.length - 1] >= values[0] ? '#fb7185' : '#34d399'
  const lineColor = color === '#38bdf8' ? trendColor : color
  const d = points
    .map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`)
    .join(' ')
  const [lx, ly] = points[points.length - 1]

  return (
    <svg width={width} height={height}>
      <path d={d} fill="none" stroke={lineColor} strokeWidth="1.4"
        strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={lx} cy={ly} r="1.8" fill={lineColor} />
    </svg>
  )
}
