import { useEffect, useState } from 'react'

const LS_KEY = 'astock_hub_last_stock'

/** 记住上次查询的股票（借鉴 tickflow 前端 useLastStock 的习惯） */
export function useLastStock(): [string, (code: string) => void] {
  const [last, setLast] = useState<string>(() => {
    try {
      return localStorage.getItem(LS_KEY) ?? ''
    } catch {
      return ''
    }
  })

  const store = (code: string) => {
    setLast(code)
    try {
      localStorage.setItem(LS_KEY, code)
    } catch {
      /* ignore */
    }
  }

  return [last, store]
}

/** 定时轮询：running 为 true 时每 4s 拉一次 */
export function usePolling(active: boolean, fn: () => void, intervalMs = 4000) {
  useEffect(() => {
    if (!active) return
    fn()
    const timer = setInterval(fn, intervalMs)
    return () => clearInterval(timer)
  }, [active, fn, intervalMs])
}
// 因子相关 query keys（从 tickflow 移植）
export const QK = {
  screenerStrategies: (kind?: string) => ['screener-strategies', kind ?? 'all'] as const,
  screenerCachedSummary: ['screener-cached-summary'] as const,
  screenerCached: (date?: string, cols?: string) => ['screener-cached', date ?? '', cols ?? ''] as const,
  screenerCachedResult: (strategyId: string, date?: string, cols?: string) =>
    ['screener-cached-result', strategyId, date ?? '', cols ?? ''] as const,
  factorLibrary: ['factor-library'] as const,
  factorDetail: (id: string) => ['factor-detail', id] as const,
  factorTrial: (formula: string) => ['factor-trial', formula] as const,
  factorCustom: ['factor-custom'] as const,
} as const
