export type Theme = 'dark' | 'light'

export interface ChartTheme {
  name: Theme
  text: string
  textStrong: string
  grid: string
  border: string
  crosshair: string
  crosshairLabelBg: string
  tooltipBg: string
  tooltipBorder: string
  tooltipText: string
  infoBarBg: string
  zoomFill: string
  fillSubtle: string
}

export function normalizeTheme(value: unknown): Theme {
  return value === 'light' ? 'light' : 'dark'
}

export function nextTheme(theme: Theme): Theme {
  return theme === 'dark' ? 'light' : 'dark'
}

const DARK: ChartTheme = {
  name: 'dark',
  text: '#94a3b8', textStrong: '#e2e8f0', grid: '#1e293b', border: '#334155',
  crosshair: 'rgba(255,255,255,0.25)', crosshairLabelBg: '#334155',
  tooltipBg: 'rgba(17,26,46,0.97)', tooltipBorder: '#334155', tooltipText: '#e2e8f0',
  infoBarBg: 'rgba(22,33,58,0.85)', zoomFill: 'rgba(255,255,255,0.06)',
  fillSubtle: 'rgba(255,255,255,0.04)',
}

const LIGHT: ChartTheme = {
  name: 'light',
  text: '#64748b', textStrong: '#334155', grid: '#e2e8f0', border: '#cbd5e1',
  crosshair: 'rgba(15,23,42,0.3)', crosshairLabelBg: '#475569',
  tooltipBg: 'rgba(255,255,255,0.97)', tooltipBorder: '#cbd5e1', tooltipText: '#334155',
  infoBarBg: 'rgba(241,245,249,0.9)', zoomFill: 'rgba(100,116,139,0.12)',
  fillSubtle: 'rgba(148,163,184,0.08)',
}

export function chartTheme(theme: Theme): ChartTheme {
  return theme === 'dark' ? DARK : LIGHT
}
