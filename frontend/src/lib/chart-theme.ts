/**
 * 图表主题 —— lightweight-charts 等 JS 图表从 CSS 变量取色，
 * 随浅色/深色主题（display store）切换，与界面变量保持单一来源。
 */

import { useEffect, useState } from "react"
import { useDisplayStore } from "@/stores/display"

export interface ChartTheme {
  /** 图表背景 */
  background: string
  /** 坐标文字/刻度 */
  text: string
  /** 网格线 */
  grid: string
  /** 网格线（柔和半透明，权益曲线等低干扰场景） */
  gridSoft: string
  /** 坐标轴边框 */
  border: string
  /** 十字准星 */
  crosshair: string
  /** 十字准星（柔和半透明） */
  crosshairSoft: string
  /** 十字准星标签底色（主题色） */
  crosshairLabel: string
  /** 浮层提示底色 */
  tooltipBg: string
}

/** 浅色兜底值（与 globals.css :root 一致；SSR/读取失败时使用） */
const LIGHT_FALLBACK: ChartTheme = {
  background: "#ffffff",
  text: "#5a6270",
  grid: "#e9edf2",
  gridSoft: "rgba(23, 26, 31, 0.06)",
  border: "#dde2e9",
  crosshair: "#98a0ad",
  crosshairSoft: "rgba(90, 98, 112, 0.35)",
  crosshairLabel: "#2563eb",
  tooltipBg: "rgba(23, 26, 31, 0.92)",
}

function readVar(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback
  try {
    const value = getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim()
    return value || fallback
  } catch {
    return fallback
  }
}

/** 读取当前主题的图表配色（调用方保证在浏览器环境） */
export function readChartTheme(): ChartTheme {
  return {
    background: readVar("--chart-bg", LIGHT_FALLBACK.background),
    text: readVar("--chart-text", LIGHT_FALLBACK.text),
    grid: readVar("--chart-grid", LIGHT_FALLBACK.grid),
    gridSoft: readVar("--chart-grid-soft", LIGHT_FALLBACK.gridSoft),
    border: readVar("--chart-border", LIGHT_FALLBACK.border),
    crosshair: readVar("--chart-crosshair", LIGHT_FALLBACK.crosshair),
    crosshairSoft: readVar("--chart-crosshair-soft", LIGHT_FALLBACK.crosshairSoft),
    crosshairLabel: readVar("--primary", LIGHT_FALLBACK.crosshairLabel),
    tooltipBg: readVar("--chart-tooltip-bg", LIGHT_FALLBACK.tooltipBg),
  }
}

/**
 * 订阅主题的图表配色 hook。
 * 主题切换后等一帧（CSS 变量块已应用）再读取，返回新对象即触发调用方重挂图表配置。
 */
export function useChartTheme(): ChartTheme {
  const theme = useDisplayStore((s) => s.theme)
  const [chartTheme, setChartTheme] = useState<ChartTheme>(LIGHT_FALLBACK)

  useEffect(() => {
    // 首帧：水合后按当前主题取色
    const id = requestAnimationFrame(() => setChartTheme(readChartTheme()))
    return () => cancelAnimationFrame(id)
  }, [theme])

  return chartTheme
}
