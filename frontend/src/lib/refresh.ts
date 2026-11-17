import { useEffect, useState } from 'react'

/**
 * 分时段刷新节奏（盘中高频、非盘中低频兜底）：
 * 盘中（9:15-15:05）用 fastMs，其余时间 30 分钟兜底（盘后数据等 16:00 链刷新）。
 * 依据：实时重算的瓶颈是上游快照拉取，分钟级已够决策；非盘中数据不变。
 */
export function sessionRefreshMs(fastMs = 90_000): number {
  const hm = new Date().getHours() * 60 + new Date().getMinutes()
  if (hm >= 9 * 60 + 15 && hm <= 15 * 60 + 5) return fastMs
  return 30 * 60_000
}

/** 每分钟强制重渲染，让 sessionRefreshMs 的时段边界（9:15/15:05）随时间生效。 */
export function useMinuteTick() {
  const [, setTick] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), 60_000)
    return () => clearInterval(t)
  }, [])
}
