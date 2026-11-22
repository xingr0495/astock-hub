import { useMemo } from 'react'
import ReactECharts from 'echarts-for-react'
import { useChartTheme } from '../lib/theme'
import { applyChartTheme } from '../lib/chart-theme-option'

/** 全站 ECharts 统一主题入口：业务 option 不变，主题相关可读性颜色集中处理。 */
export default function ThemedECharts({ option, ...props }: any) {
  const ct = useChartTheme()
  const themed = useMemo(() => applyChartTheme(option, ct), [option, ct])
  return <ReactECharts option={themed} {...props} />
}
