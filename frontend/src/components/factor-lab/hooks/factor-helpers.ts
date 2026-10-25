/** 因子实验室纯辅助函数 —— 不含 React 状态 */

import type {
  Champion,
  FactorFavoriteItem,
  FactorHistoryItem,
} from "@/lib/factor-lab-api"
import type { CreateTaskPayload } from "@/lib/ai-trading-api"
import type { SearchFormPayload } from "../factor-search-form"

/** 默认搜索请求（回退用）
 *
 * 防过拟合默认开启推荐配置：训练比例 0.7 + walk-forward 3 折。
 * 与 factor-search-form 的 state 默认值、后端 schema 默认值保持一致，
 * 三处同源避免行为分裂。
 */
export function defaultSearchPayload(
  symbol: string,
  timeframe: string,
): SearchFormPayload {
  return {
    symbol,
    timeframe,
    population: 30,
    generations: 15,
    top_n: 10,
    seed: 42,
    cost: null,
    use_llm_coach: false,
    model_row_id: null,
    train_ratio: 0.7,
    test_recent_bars: 0,
    walk_forward_folds: 3,
  }
}

/** AI 交易建任务 payload（因子策略） */
export function buildFactorTaskPayload(
  symbol: string,
  timeframe: string,
  tokens: number[],
): CreateTaskPayload {
  return {
    name: `因子·${symbol}·${timeframe}`,
    model_row_id: null,
    strategy_type: "factor",
    strategy_params: { factor_tokens: tokens },
    symbol,
    symbol_name: "",
    timeframe,
    side_mode: "both",
    position_mode: "fixed_qty",
    fixed_qty: 1,
    close_rules: {
      pnl_pct: null,
      total_pnl_pct: null,
      // 日线因子持仓以日为单位，日内强平会推翻回测的持仓假设；
      // 分钟因子维持日内平仓（与回测 bar 内持仓假设一致）
      session_close: timeframe !== "1d",
      ai_auto: false,
    },
    stop_rules: { loss_pct: null, loss_amount: null, ai_auto: false },
    close_on_stop: true,
    auto_start: false,
  }
}

/** AI 交易建任务 payload（组合因子：2-5 个低相关因子等权） */
export function buildComboTaskPayload(
  symbol: string,
  timeframe: string,
  tokenGroups: number[][],
): CreateTaskPayload {
  const base = buildFactorTaskPayload(symbol, timeframe, tokenGroups[0] ?? [])
  return {
    ...base,
    name: `组合(${tokenGroups.length})·${symbol}·${timeframe}`,
    // factor_tokens 传 list of lists = 组合形态；省略 factor_weights = 等权
    strategy_params: { factor_tokens: tokenGroups },
  }
}

/** 默认空指标（补齐字段用） */
export function emptyMetrics(composite: number): Champion["metrics"] {
  return {
    ann_ret: 0,
    sortino: 0,
    calmar: 0,
    ts_ic: 0,
    symmetry: 0,
    turnover_q: 0,
    oos_sortino: 0,
    oos_mult: 1,
    oos_negative: false,
    consistency: 0,
    composite,
    avg_turnover: 0,
    exposure: 0,
  }
}

/** 历史记录 → Champion（补齐指标） */
export function championFromHistory(item: FactorHistoryItem): Champion {
  const m = item.metrics as Record<string, unknown>
  return {
    tokens: item.tokens,
    text: item.text,
    composite: item.composite,
    metrics: {
      ...emptyMetrics(item.composite),
      ann_ret: Number(m.ann_ret ?? 0),
      sortino: Number(m.sortino ?? 0),
      calmar: Number(m.calmar ?? 0),
      ts_ic: Number(m.ts_ic ?? 0),
      oos_sortino: Number(m.oos_sortino ?? 0),
      oos_negative: Boolean(m.oos_negative ?? false),
      composite: Number(m.composite ?? item.composite),
      // 防过拟合子键：旧历史记录无这些字段，存在才透传，否则保持 undefined
      train_metrics: (m.train_metrics ?? undefined) as
        | Champion["metrics"]["train_metrics"]
        | undefined,
      test_metrics: (m.test_metrics ?? undefined) as
        | Champion["metrics"]["test_metrics"]
        | undefined,
      walk_forward: (m.walk_forward ?? undefined) as
        | Champion["metrics"]["walk_forward"]
        | undefined,
    },
  }
}

/** 收藏 → Champion */
export function championFromFavorite(item: FactorFavoriteItem): Champion {
  return {
    tokens: item.tokens,
    text: item.text,
    composite: item.composite ?? 0,
    metrics: {
      ...emptyMetrics(item.composite ?? 0),
      ...(item.metrics as object),
    },
  }
}
