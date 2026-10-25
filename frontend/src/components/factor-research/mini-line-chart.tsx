"use client"

/**
 * 迷你折线图 —— lightweight-charts 单序列（因子研究页：IC衰减/多空净值）
 * 主题感知：跟随 CSS 图表变量
 */

import { useEffect, useRef } from "react"
import {
  createChart,
  LineSeries,
  type IChartApi,
  type Time,
} from "lightweight-charts"
import { makeChartOpts } from "@/components/market/kline/utils"
import { useDisplayStore } from "@/stores/display"

export interface LinePoint {
  x: string | number
  y: number
}

function makeMiniChartOpts() {
  const options = makeChartOpts()
  return { ...options, timeScale: { ...options.timeScale, rightOffset: 0, rightOffsetPixels: 0 } }
}

export function MiniLineChart({
  points,
  color = "#3b82f6",
  height = 220,
  valueFormat = (v: number) => v.toFixed(4),
}: {
  points: LinePoint[]
  color?: string
  height?: number
  valueFormat?: (v: number) => string
}): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const theme = useDisplayStore((s) => s.theme)

  useEffect(() => {
    if (!containerRef.current || points.length === 0) return
    const data = points.map((point) => {
      const time = typeof point.x === "number" ? point.x : toUtcTimestamp(point.x)
      if (!Number.isFinite(time) || !Number.isFinite(point.y)) throw new RangeError("研究图表时间或数值无效")
      return { time: time as Time, value: point.y }
    })
    const chart = createChart(containerRef.current, {
      ...makeMiniChartOpts(),
      width: containerRef.current.clientWidth,
      height,
    })
    chartRef.current = chart
    const series = chart.addSeries(LineSeries, {
      color,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: true,
    })
    // 数值过小/过大时给一个可读的价格格式
    series.applyOptions({
      priceFormat: {
        type: "custom",
        formatter: valueFormat,
      },
    })
    series.setData(data)
    chart.timeScale().fitContent()

    const ro = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth })
        chart.timeScale().fitContent()
      }
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [points, color, height, valueFormat])

  useEffect(() => {
    chartRef.current?.applyOptions(makeMiniChartOpts())
    chartRef.current?.timeScale().fitContent()
  }, [theme])

  return (
    <div
      ref={containerRef}
      className="w-full rounded-lg border border-[var(--border)] bg-[var(--chart-bg)]"
      style={{ height }}
    />
  )
}

function toUtcTimestamp(dateStr: string): number {
  if (!/^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:\d{2})?)?$/.test(dateStr)) throw new RangeError("研究图表日期无效")
  const date = dateStr.slice(0, 10)
  const midnight = Date.parse(`${date}T00:00:00Z`)
  if (!Number.isFinite(midnight) || new Date(midnight).toISOString().slice(0, 10) !== date) throw new RangeError("研究图表日期无效")
  const normalized = dateStr.length === 10 ? `${date}T00:00:00Z` : dateStr.replace(" ", "T")
  const timestamp = Date.parse(/(?:Z|[+-]\d{2}:\d{2})$/.test(normalized) ? normalized : `${normalized}Z`)
  if (!Number.isFinite(timestamp)) throw new RangeError("研究图表日期无效")
  return Math.floor(timestamp / 1000)
}
