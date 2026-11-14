import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, CustomChart, HeatmapChart, LineChart, PieChart, RadarChart } from 'echarts/charts'
import {
  GridComponent, TitleComponent, MarkLineComponent, MarkPointComponent, RadarComponent, TooltipComponent, VisualMapComponent,
  DataZoomComponent,
} from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'

echarts.use([
  BarChart, LineChart, PieChart, RadarChart, HeatmapChart, CustomChart,
  GridComponent, TitleComponent, TooltipComponent, RadarComponent, VisualMapComponent, MarkLineComponent, MarkPointComponent,
  DataZoomComponent, SVGRenderer,
])

interface Props {
  option: EChartsOption
  size?: 'sm' | 'md' | 'lg'
  height?: number
  label: string                 // accessible name: what the chart shows
}

/** One ECharts instance per mount; each new option (view switch, theme change) animates in. */
export function EChart({ option, size, height, label }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!ref.current) return
    const c = echarts.init(ref.current, undefined, { renderer: 'svg' })
    chart.current = c
    const ro = new ResizeObserver(() => c.resize({ animation: { duration: 200 } }))
    ro.observe(ref.current)
    return () => { ro.disconnect(); c.dispose(); chart.current = null }
  }, [])

  useEffect(() => {
    // honour reduced motion: charts appear in their final state
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    chart.current?.setOption(still ? { ...option, animation: false } : option, { notMerge: true })
  }, [option])

  return (
    <div
      ref={ref}
      role="img"
      aria-label={label}
      className={`chart ${size ?? ''}`}
      style={height ? { height } : undefined}
    />
  )
}
