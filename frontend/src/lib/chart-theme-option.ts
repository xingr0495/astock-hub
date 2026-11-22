import type { ChartTheme } from './theme-model'

function mapNode(node: any, fn: (value: any) => any) {
  if (Array.isArray(node)) return node.map(fn)
  return node ? fn(node) : node
}

function themedAxis(axis: any, ct: ChartTheme) {
  return {
    ...axis,
    axisLabel: { ...axis.axisLabel, color: ct.text },
    nameTextStyle: axis.nameTextStyle ? { ...axis.nameTextStyle, color: ct.text } : axis.nameTextStyle,
    axisLine: { ...axis.axisLine, lineStyle: { ...axis.axisLine?.lineStyle, color: ct.border } },
    axisTick: axis.axisTick ? { ...axis.axisTick, lineStyle: { ...axis.axisTick?.lineStyle, color: ct.border } } : axis.axisTick,
    splitLine: axis.splitLine ? { ...axis.splitLine, lineStyle: { ...axis.splitLine?.lineStyle, color: ct.grid } } : axis.splitLine,
  }
}

/** ECharts 可读性颜色统一换肤；series/数据/涨跌色原样保留。 */
export function applyChartTheme(option: any, ct: ChartTheme): any {
  if (ct.name === 'dark') return option  // 默认夜间视觉必须与原图表逐值一致
  return {
    ...option,
    backgroundColor: option.backgroundColor ?? 'transparent',
    textStyle: { ...option.textStyle, color: ct.text },
    xAxis: mapNode(option.xAxis, (axis) => themedAxis(axis, ct)),
    yAxis: mapNode(option.yAxis, (axis) => themedAxis(axis, ct)),
    legend: mapNode(option.legend, (legend) => ({
      ...legend, textStyle: { ...legend.textStyle, color: ct.text },
    })),
    tooltip: option.tooltip ? {
      ...option.tooltip, backgroundColor: ct.tooltipBg, borderColor: ct.tooltipBorder,
      textStyle: { ...option.tooltip.textStyle, color: ct.tooltipText },
      axisPointer: option.tooltip.axisPointer ? {
        ...option.tooltip.axisPointer,
        lineStyle: { ...option.tooltip.axisPointer.lineStyle, color: ct.crosshair },
        crossStyle: { ...option.tooltip.axisPointer.crossStyle, color: ct.crosshair },
        label: { ...option.tooltip.axisPointer.label, backgroundColor: ct.crosshairLabelBg },
      } : option.tooltip.axisPointer,
    } : option.tooltip,
    dataZoom: mapNode(option.dataZoom, (zoom) => ({
      ...zoom, borderColor: ct.border, fillerColor: ct.zoomFill,
      textStyle: { ...zoom.textStyle, color: ct.text },
    })),
    visualMap: mapNode(option.visualMap, (map) => ({
      ...map, textStyle: { ...map.textStyle, color: ct.text },
    })),
  }
}
