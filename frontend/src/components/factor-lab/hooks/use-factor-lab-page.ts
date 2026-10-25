"use client"

/**
 * 因子实验室页面状态与操作
 */

import { useCallback, useState } from "react"
import type { SearchFormPayload } from "../factor-search-form"
import {
  addFactorFavorite,
  backtestFactor,
  deleteFactorFavorite,
  deleteFactorHistory,
  llmGenerateFactors,
  searchFactors,
  type Champion,
  type FactorBacktestResult,
  type FactorMetrics,
  type SearchResult,
} from "@/lib/factor-lab-api"
import { createAITradingTask as createTask } from "@/lib/ai-trading-api"
import { useFactorLabData } from "./use-factor-data"
import {
  buildComboTaskPayload,
  buildFactorTaskPayload,
  defaultSearchPayload,
} from "./factor-helpers"

/** 收藏入参（Champion 或历史记录都可适配） */
export interface FavoriteInput {
  tokens: number[]
  text: string
  symbol: string
  timeframe: string
  composite: number
  metrics: Partial<FactorMetrics>
}

export interface FactorLabPageState {
  loading: boolean
  btLoading: boolean
  genLoading: boolean
  result: SearchResult | null
  selected: Champion | null
  bt: FactorBacktestResult | null
  error: string | null
  lastReq: SearchFormPayload | null
  building: boolean
  buildMsg: string | null
  symbol: string
  setSymbol: (s: string) => void
  setBuildMsg: (s: string | null) => void
  handleSearch: (p: SearchFormPayload) => Promise<void>
  handleEvolve: () => Promise<void>
  handleGenerate: (payload: {
    model_row_id: string
    user_hint: string
  }) => Promise<void>
  selectFactor: (c: Champion, p?: SearchFormPayload) => Promise<void>
  handleBuildTask: () => Promise<void>
  handleComboMount: (champions: Champion[]) => Promise<void>
  handleFavoriteChampion: (c: Champion) => Promise<void>
  handleFavoriteHistory: (c: FavoriteInput) => Promise<void>
  /** 弹窗保存：选名称与文件夹后收藏 */
  saveFavorite: (
    item: FavoriteInput,
    opts?: { name?: string; folderId?: string | null },
  ) => Promise<void>
  /** 收藏默认名（品种-年化收益） */
  defaultFavoriteName: (item: FavoriteInput) => string
  deleteHistory: (id: string) => Promise<void>
  deleteFavorite: (id: string) => Promise<void>
}

function errOf(e: unknown, fallback: string): string {
  return e instanceof Error ? e.message : fallback
}

/** 页面状态 hook */
export function useFactorLabPage(): FactorLabPageState & ReturnType<typeof useFactorLabData> {
  const [loading, setLoading] = useState(false)
  const [btLoading, setBtLoading] = useState(false)
  const [genLoading, setGenLoading] = useState(false)
  const [result, setResult] = useState<SearchResult | null>(null)
  const [selected, setSelected] = useState<Champion | null>(null)
  const [bt, setBt] = useState<FactorBacktestResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lastReq, setLastReq] = useState<SearchFormPayload | null>(null)
  const [building, setBuilding] = useState(false)
  const [buildMsg, setBuildMsg] = useState<string | null>(null)
  const [symbol, setSymbol] = useState("")
  const data = useFactorLabData(symbol)

  const refreshHistory = data.refreshHistory
  const refreshFavorites = data.refreshFavorites
  const contractNameOf = data.contractNameOf

  const selectFactor = useCallback(
    async (c: Champion, p?: SearchFormPayload): Promise<void> => {
      setSelected(c)
      setBt(null)
      setBtLoading(true)
      const sym = p?.symbol ?? lastReq?.symbol ?? result?.symbol ?? symbol
      const tf = p?.timeframe ?? lastReq?.timeframe ?? result?.timeframe ?? "1d"
      // 与触发本次选择时的搜索/表单保持同一区间（含长历史模式）
      const startDate = p?.start_date ?? lastReq?.start_date
      const endDate = p?.end_date ?? lastReq?.end_date
      try {
        setBt(
          await backtestFactor({
            symbol: sym,
            timeframe: tf,
            factor_tokens: c.tokens,
            ...(startDate && endDate ? { start_date: startDate, end_date: endDate } : {}),
          }),
        )
      } catch (e) {
        setError(errOf(e, "回测失败"))
      } finally {
        setBtLoading(false)
      }
    },
    [lastReq, result, symbol],
  )

  async function applySearchResult(
    r: SearchResult,
    p: SearchFormPayload,
  ): Promise<void> {
    setResult(r)
    setLastReq(p)
    setSymbol(p.symbol)
    if (r.champions[0]) await selectFactor(r.champions[0], p)
    void refreshHistory(p.symbol)
  }

  async function handleSearch(p: SearchFormPayload): Promise<void> {
    setLoading(true)
    setError(null)
    setResult(null)
    setSelected(null)
    setBt(null)
    setLastReq(p)
    setSymbol(p.symbol)
    try {
      const r = await searchFactors({
        symbol: p.symbol,
        timeframe: p.timeframe,
        population: p.population,
        generations: p.generations,
        top_n: p.top_n,
        seed: p.seed,
        cost: p.cost,
        use_llm_coach: p.use_llm_coach,
        model_row_id: p.model_row_id,
        train_ratio: p.train_ratio,
        test_recent_bars: p.test_recent_bars,
        walk_forward_folds: p.walk_forward_folds,
        ...(p.start_date && p.end_date
          ? { start_date: p.start_date, end_date: p.end_date }
          : {}),
      })
      await applySearchResult(r, p)
    } catch (e) {
      setError(errOf(e, "搜索失败"))
    } finally {
      setLoading(false)
    }
  }

  async function handleEvolve(): Promise<void> {
    if (!lastReq || !result?.champions.length) return
    const seeds = result.champions.slice(0, 5).map((c) => c.tokens)
    const p = { ...lastReq }
    setLoading(true)
    setError(null)
    try {
      const r = await searchFactors({
        symbol: p.symbol,
        timeframe: p.timeframe,
        population: p.population,
        generations: p.generations,
        top_n: p.top_n,
        seed: p.seed + 1,
        cost: p.cost,
        seed_tokens: seeds,
        use_llm_coach: p.use_llm_coach,
        model_row_id: p.model_row_id,
        train_ratio: p.train_ratio,
        test_recent_bars: p.test_recent_bars,
        walk_forward_folds: p.walk_forward_folds,
        ...(p.start_date && p.end_date
          ? { start_date: p.start_date, end_date: p.end_date }
          : {}),
      })
      await applySearchResult(r, { ...p, seed: p.seed + 1 })
    } catch (e) {
      setError(errOf(e, "进化失败"))
    } finally {
      setLoading(false)
    }
  }

  async function handleGenerate(payload: {
    model_row_id: string
    user_hint: string
  }): Promise<void> {
    const sym = (lastReq?.symbol || symbol).trim().toLowerCase()
    const tf = lastReq?.timeframe || "1d"
    if (!sym) {
      setError("请先选择合约")
      return
    }
    setGenLoading(true)
    setError(null)
    try {
      const r = await llmGenerateFactors({
        symbol: sym,
        timeframe: tf,
        model_row_id: payload.model_row_id,
        user_hint: payload.user_hint || undefined,
        seed_tokens: result?.champions.slice(0, 3).map((c) => c.tokens),
        ...(lastReq?.start_date && lastReq?.end_date
          ? { start_date: lastReq.start_date, end_date: lastReq.end_date }
          : {}),
      })
      const p = lastReq ?? defaultSearchPayload(sym, tf)
      await applySearchResult(r, p)
      if (!r.champions.length) setError("未生成可用因子，可换模型或提示后再试")
    } catch (e) {
      setError(errOf(e, "生成失败"))
    } finally {
      setGenLoading(false)
    }
  }

  async function handleBuildTask(): Promise<void> {
    if (!selected || !lastReq) return
    if (selected.metrics?.overfit_warning) {
      setError("该因子未通过样本外验证（测试段亏损），已禁止挂载实盘")
      return
    }
    setBuilding(true)
    setBuildMsg(null)
    try {
      const task = await createTask(
        buildFactorTaskPayload(lastReq.symbol, lastReq.timeframe, selected.tokens),
      )
      setBuildMsg(`已创建：${task.name}（到 AI 交易页启动）`)
    } catch (e) {
      setBuildMsg(errOf(e, "创建失败"))
    } finally {
      setBuilding(false)
    }
  }

  async function handleComboMount(chosen: Champion[]): Promise<void> {
    if (!lastReq) return
    if (chosen.length < 2 || chosen.length > 5) {
      setError("组合需勾选 2-5 个因子")
      return
    }
    const bad = chosen.find((c) => c.metrics?.overfit_warning)
    if (bad) {
      setError("组合成员含未通过样本外验证的因子（测试段亏损），已禁止挂载")
      return
    }
    setBuilding(true)
    setBuildMsg(null)
    try {
      const payload = buildComboTaskPayload(
        lastReq.symbol,
        lastReq.timeframe,
        chosen.map((c) => c.tokens),
      )
      const task = await createTask(payload)
      setBuildMsg(`已创建组合任务：${task.name}（到 AI 交易页启动）`)
    } catch (e) {
      setBuildMsg(errOf(e, "创建失败"))
    } finally {
      setBuilding(false)
    }
  }

  async function favoriteFrom(
    item: FavoriteInput,
    opts?: { name?: string; folderId?: string | null },
  ): Promise<void> {
    if (item.metrics?.overfit_warning) {
      setError("该因子未通过样本外验证（测试段亏损），已禁止收藏")
      return
    }
    // 默认名：品种中文名-年化收益:xx%
    const annRet = Number(item.metrics?.ann_ret ?? 0)
    const annPct = Number.isFinite(annRet) ? (annRet * 100).toFixed(1) : "0.0"
    const defaultName = `${contractNameOf(item.symbol)}-年化收益:${annPct}%`
    try {
      await addFactorFavorite({
        tokens: item.tokens,
        text: item.text,
        name: opts?.name?.trim() || defaultName,
        symbol: item.symbol,
        timeframe: item.timeframe,
        composite: item.composite,
        metrics: item.metrics,
        folder_id: opts?.folderId ?? null,
      })
      setBuildMsg(
        opts?.folderId ? "已收藏到指定文件夹" : "已加入收藏",
      )
      void refreshFavorites(symbol)
    } catch (e) {
      setError(errOf(e, "收藏失败"))
    }
  }

  /** 收藏弹窗用默认名（品种-年化收益） */
  function defaultFavoriteName(item: FavoriteInput): string {
    const annRet = Number(item.metrics?.ann_ret ?? 0)
    const annPct = Number.isFinite(annRet) ? (annRet * 100).toFixed(1) : "0.0"
    return `${contractNameOf(item.symbol)}-年化收益:${annPct}%`
  }

  async function handleFavoriteChampion(c: Champion): Promise<void> {
    await favoriteFrom({
      tokens: c.tokens,
      text: c.text,
      symbol: lastReq?.symbol ?? symbol,
      timeframe: lastReq?.timeframe ?? result?.timeframe ?? "",
      composite: c.composite,
      metrics: c.metrics,
    })
  }

  async function handleFavoriteHistory(item: FavoriteInput): Promise<void> {
    await favoriteFrom(item)
  }

  return {
    loading,
    btLoading,
    genLoading,
    result,
    selected,
    bt,
    error,
    lastReq,
    building,
    buildMsg,
    symbol,
    setSymbol,
    setBuildMsg,
    handleSearch,
    handleEvolve,
    handleGenerate,
    selectFactor,
    handleBuildTask,
    handleComboMount,
    handleFavoriteChampion,
    handleFavoriteHistory,
    saveFavorite: favoriteFrom,
    defaultFavoriteName,
    deleteHistory: async (id: string) => {
      try {
        await deleteFactorHistory(id)
        void refreshHistory(symbol)
      } catch (e) {
        setError(errOf(e, "删除失败"))
      }
    },
    deleteFavorite: async (id: string) => {
      try {
        await deleteFactorFavorite(id)
        void refreshFavorites(symbol)
      } catch (e) {
        setError(errOf(e, "取消收藏失败"))
      }
    },
    ...data,
  }
}
