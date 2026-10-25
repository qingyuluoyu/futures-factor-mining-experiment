// type-only 导入：运行时擦除，types 与 stores 无环
import type { useIndicatorStore } from "@/stores/indicator"
import type { useBacktestIndicatorStore } from "@/stores/backtest-indicator"
import type { useAiMarketIndicatorStore } from "@/stores/ai-market-indicator"
// 强弱类型（V1/V2 配置与版本开关）自本目录 indicator-strength 导入并 re-export
import type { StrengthConfig, StrengthV2Config, StrengthVersion } from "./indicator-strength"

/** 线型：实线 / 虚线（映射 lightweight-charts LineStyle） */
export type IndicatorLineStyle = "solid" | "dashed"

/** 均线配置 */
export interface MALineConfig {
  /** 均线周期（2-500） */
  period: number
  /** 均线颜色（十六进制） */
  color: string
}

/** MACD 配置 */
export interface MACDConfig {
  /** 是否启用 MACD */
  enabled: boolean
  /** 快线周期（2-200） */
  fastPeriod: number
  /** 慢线周期（2-200） */
  slowPeriod: number
  /** 信号线周期（2-200） */
  signalPeriod: number
  /** DIF 线颜色 */
  difColor: string
  /** DEA 线颜色 */
  deaColor: string
  /** 柱状图上涨色（macd≥0） */
  histUpColor: string
  /** 柱状图下跌色（macd<0） */
  histDownColor: string
}

/** RSI 配置（超买超卖） */
export interface RSIConfig {
  /** 是否启用 RSI 副图 */
  enabled: boolean
  /** RSI 周期（2-200），默认 14 */
  period: number
  /** 超买线（默认 70） */
  overbought: number
  /** 超卖线（默认 30） */
  oversold: number
  /** RSI 主线颜色 */
  lineColor: string
  /** 超买参考线颜色 */
  overboughtColor: string
  /** 超卖参考线颜色 */
  oversoldColor: string
}

/** 布林带配置（主图轨道） */
export interface BOLLConfig {
  /** 是否启用布林带 */
  enabled: boolean
  /** 中轨 SMA 周期（2-200），默认 20 */
  period: number
  /** 标准差倍数（0.5-5），默认 2 */
  std: number
  /** 上轨颜色 */
  upperColor: string
  /** 中轨颜色 */
  middleColor: string
  /** 下轨颜色 */
  lowerColor: string
  /** 上轨线型 */
  upperStyle: IndicatorLineStyle
  /** 中轨线型 */
  middleStyle: IndicatorLineStyle
  /** 下轨线型 */
  lowerStyle: IndicatorLineStyle
}

/** JDK（KDJ）配置 —— 国内期货常用随机指标 */
export interface JDKConfig {
  /** 是否启用 JDK 副图 */
  enabled: boolean
  /** RSV 周期（2-200），默认 9 */
  rsvPeriod: number
  /** K 平滑周期（2-50），默认 3 */
  kPeriod: number
  /** D 平滑周期（2-50），默认 3 */
  dPeriod: number
  /** K 线颜色 */
  kColor: string
  /** D 线颜色 */
  dColor: string
  /** J 线颜色 */
  jColor: string
}

/** 指标总配置 */
export interface IndicatorConfig {
  /** 均线列表（0-5 条） */
  maLines: MALineConfig[]
  /** MACD 配置 */
  macd: MACDConfig
  /** RSI 配置 */
  rsi: RSIConfig
  /** 布林带配置 */
  boll: BOLLConfig
  /** JDK（KDJ）配置 */
  jdk: JDKConfig
  /** 强弱指标 V1 配置 */
  strength: StrengthConfig
  /** 强弱指标 V2 配置（形态档：衰竭 / 确认 / 中继，双向） */
  strengthV2: StrengthV2Config
  /** 当前生效的强弱版本（二选一；存量用户默认 v1，行为不变） */
  strengthVersion: StrengthVersion
}

/** 线型 → lightweight-charts LineStyle 数值 */
export function toChartLineStyle(style: IndicatorLineStyle): number {
  // 0 Solid, 2 Dashed（见 lightweight-charts LineStyle）
  return style === "dashed" ? 2 : 0
}

/**
 * 指标配置 store hook 联合（唯一实现，此前在 dialog / kline-chart / 各 tab
 * 手写了 5 份且不一致——tab 那几份漏了 AI 看盘 store）。
 * 分屏 2-4 的工厂 store 与本地隔离 store 结构同构，可直接传入。
 */
export type IndicatorStoreHook =
  | typeof useIndicatorStore
  | typeof useBacktestIndicatorStore
  | typeof useAiMarketIndicatorStore

// 强弱类型（V1 配置 / V2 配置 / 版本开关）拆至 indicator-strength.ts、
// 预设颜色与默认配置拆至 indicator-defaults.ts（本文件守住 300 行上限）；
// re-export 保持既有 `from "@/types/indicator"` 导入路径不变
export type {
  StrengthConfig,
  StrengthV2Config,
  StrengthVersion,
} from "./indicator-strength"
export { DEFAULT_INDICATOR_CONFIG, PRESET_COLORS } from "./indicator-defaults"
