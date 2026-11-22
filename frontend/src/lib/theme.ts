import { useEffect, useState } from 'react'
import { chartTheme, nextTheme, type ChartTheme, type Theme } from './theme-model.ts'

const KEY = 'ah-theme'
const EVENT = 'ah-theme-change'
let memoryTheme: Theme | null = null

function readStoredTheme(): Theme | null {
  try {
    const value = localStorage.getItem(KEY)
    return value === 'light' || value === 'dark' ? value : null
  } catch { return null }
}

// 默认跟随面板颜色（用户拍板"默认跟随面板颜色"）：localStorage 无显式选择时，
// 读 <html> 上已应用的 dark class（面板当前实际颜色），不再读 prefers-color-scheme。
function panelTheme(): Theme {
  try {
    return document.documentElement.classList.contains('dark') ? 'dark' : 'light'
  } catch { return 'dark' }
}

export function getTheme(): Theme {
  return memoryTheme ?? readStoredTheme() ?? panelTheme()
}

export function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle('dark', theme === 'dark')
  window.dispatchEvent(new CustomEvent(EVENT, { detail: theme }))
}

export function setTheme(theme: Theme) {
  memoryTheme = theme
  try { localStorage.setItem(KEY, theme) } catch { /* 存储失败仍保留本页内存主题 */ }
  applyTheme(theme)
}

export function toggleTheme(): Theme {
  const next = nextTheme(getTheme())
  setTheme(next)
  return next
}

export function useTheme(): Theme {
  const [theme, set] = useState<Theme>(getTheme)
  useEffect(() => {
    const render = (value: Theme) => {
      memoryTheme = value
      document.documentElement.classList.toggle('dark', value === 'dark')
      set(value)
    }
    const onCustom = (event: Event) => {
      const value = (event as CustomEvent).detail
      if (value === 'light' || value === 'dark') render(value)
    }
    const onStorage = () => render(readStoredTheme() ?? panelTheme())
    window.addEventListener(EVENT, onCustom)
    window.addEventListener('storage', onStorage)
    return () => {
      window.removeEventListener(EVENT, onCustom)
      window.removeEventListener('storage', onStorage)
    }
  }, [])
  return theme
}

export function useChartTheme(): ChartTheme {
  return chartTheme(useTheme())
}

export type { ChartTheme, Theme }
