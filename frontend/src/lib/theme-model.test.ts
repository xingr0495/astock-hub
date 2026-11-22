import test from 'node:test'
import assert from 'node:assert/strict'
import { chartTheme, nextTheme, normalizeTheme } from './theme-model.ts'

test('非法主题值回退夜间，合法值原样保留', () => {
  assert.equal(normalizeTheme('light'), 'light')
  assert.equal(normalizeTheme('dark'), 'dark')
  assert.equal(normalizeTheme('system'), 'dark')
  assert.equal(normalizeTheme(null), 'dark')
  assert.equal(normalizeTheme(undefined), 'dark')
})

test('日夜主题循环切换', () => {
  assert.equal(nextTheme('dark'), 'light')
  assert.equal(nextTheme('light'), 'dark')
})

test('日间与夜间图表文字和提示框使用不同可读色', () => {
  const light = chartTheme('light')
  const dark = chartTheme('dark')
  assert.notEqual(light.text, dark.text)
  assert.notEqual(light.tooltipBg, dark.tooltipBg)
  assert.equal(light.tooltipText, '#334155')
  assert.equal(dark.tooltipText, '#e2e8f0')
})
