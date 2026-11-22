import test from 'node:test'
import assert from 'node:assert/strict'
import { applyChartTheme } from './chart-theme-option.ts'
import { chartTheme } from './theme-model.ts'

test('图表主题只改可读性颜色，不改系列红绿与显式配置', () => {
  const series = [{ type: 'candlestick', itemStyle: { color: '#ef4444', color0: '#10b981' } }]
  const option = {
    backgroundColor: '#111111',
    xAxis: { type: 'category', axisLabel: { color: '#old', fontSize: 10 }, splitLine: { show: false } },
    yAxis: [{ type: 'value', splitLine: { lineStyle: { color: '#old', type: 'dashed' } } }],
    tooltip: { trigger: 'axis', textStyle: { fontSize: 11 } },
    series,
  }
  const out = applyChartTheme(option, chartTheme('light'))
  assert.equal(out.backgroundColor, '#111111')
  assert.equal(out.xAxis.axisLabel.color, '#64748b')
  assert.equal(out.xAxis.axisLabel.fontSize, 10)
  assert.equal(out.xAxis.axisLine.lineStyle.color, '#cbd5e1')
  assert.equal(out.xAxis.splitLine.show, false)
  assert.equal(out.yAxis[0].splitLine.lineStyle.color, '#e2e8f0')
  assert.equal(out.yAxis[0].splitLine.lineStyle.type, 'dashed')
  assert.equal(out.tooltip.backgroundColor, 'rgba(255,255,255,0.97)')
  assert.equal(out.tooltip.textStyle.color, '#334155')
  assert.equal(out.tooltip.textStyle.fontSize, 11)
  assert.equal(out.series, series)
})

test('无提示框的仪表图不被强行添加 tooltip，夜间 option 恒等保留', () => {
  const option = { series: [{ type: 'gauge' }] }
  const out = applyChartTheme(option, chartTheme('dark'))
  assert.equal(out, option)
  assert.equal(out.tooltip, undefined)
})
