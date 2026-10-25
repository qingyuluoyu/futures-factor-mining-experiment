/**
 * 实时指标末点更新与序列自愈（自 use-realtime-kline.ts 抽出，行为不变）
 *
 * 主图 MA/BOLL 走 indicators/main.ts，副图指标遍历注册表。
 */

import type { MutableRefObject } from "react"
import type { ISeriesApi } from "lightweight-charts"
import type { IndicatorConfig } from "@/types/indicator"
import type { KlineBar, KlinePeriod } from "@/types"
import {
  updateBollLastPoints,
  updateMaLastPoints,
  writeMainAndSubData,
  type BollSeriesRefs,
} from "../indicators/main"
import {
  updateSubIndicatorsLast,
  type SubIndicatorHandle,
  type SubIndicatorId,
} from "../indicators/registry"

/** 指标更新所需的 series 引用集合 */
export interface IndicatorUpdateRefs {
  seriesRef: MutableRefObject<ISeriesApi<"Candlestick"> | null>
  volumeRef: MutableRefObject<ISeriesApi<"Histogram"> | null>
  maSeriesRef: MutableRefObject<ISeriesApi<"Line">[]>
  bollSeriesRef: MutableRefObject<BollSeriesRefs>
  subHandlesRef: MutableRefObject<Map<SubIndicatorId, SubIndicatorHandle>>
  config: IndicatorConfig
}

/**
 * 末点增量更新全部指标（MA / BOLL / 副图注册表）。forming bar 每跳调用。
 */
export function updateAllIndicators(
  refs: IndicatorUpdateRefs,
  mergedBars: KlineBar[],
  period: KlinePeriod,
): void {
  const config = refs.config
  updateMaLastPoints(
    refs.maSeriesRef.current,
    mergedBars,
    period,
    config.maLines,
  )
  if (config.boll.enabled) {
    updateBollLastPoints(
      refs.bollSeriesRef.current,
      mergedBars,
      period,
      config.boll,
    )
  }
  updateSubIndicatorsLast(
    refs.subHandlesRef.current,
    mergedBars,
    period,
    config,
  )
}

/** 恢复自愈节流：全量 setData 每次约 200+ 根，避免逐帧重设 */
const RECOVER_THROTTLE_MS = 10_000

/**
 * oldest 拒绝自愈：全量 setData 重设蜡烛/量/指标（setData 无单调性约束）。
 * 与迁移前一致：自愈不更新 React 图例标签。
 */
export function recoverSeriesData(
  refs: IndicatorUpdateRefs,
  mergedBars: KlineBar[],
  period: KlinePeriod,
  lastRecoverAtRef: MutableRefObject<number>,
): void {
  const series = refs.seriesRef.current
  const vol = refs.volumeRef.current
  if (!series || !vol) return
  const now = Date.now()
  if (now - lastRecoverAtRef.current < RECOVER_THROTTLE_MS) return
  lastRecoverAtRef.current = now
  try {
    writeMainAndSubData({
      series,
      volume: vol,
      maSeriesList: refs.maSeriesRef.current,
      bollSeries: refs.bollSeriesRef.current,
      subHandles: refs.subHandlesRef.current,
      bars: mergedBars,
      period,
      config: refs.config,
      setMaLabels: () => {
        /* 自愈路径不改图例标签 */
      },
    })
  } catch (err) {
    console.warn("[K线序列自愈失败]", err)
  }
}
