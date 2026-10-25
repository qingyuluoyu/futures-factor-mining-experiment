"use client"

/**
 * 因子实验室页面 —— 搜索 / 进化 / 历史 / 收藏 / LLM 生成
 */

import {
  FactorSearchForm,
  type SearchFormPayload,
} from "./factor-search-form"
import { ChampionTable } from "./champion-table"
import { FACTOR_HELP, FactorLabGuide, HelpTip } from "./help-tip"
import { HistoryPanel } from "./history/history-panel"
import { FavoritesPanel } from "./favorites/favorites-panel"
import { FactorFavoriteSaveDialog } from "./favorites/favorite-save-dialog"
import type { FavoriteInput } from "./hooks/use-factor-lab-page"
import { EvolveBar } from "./llm/evolve-bar"
import { GeneratePanel } from "./llm/generate-panel"
import { SelectedFactorPanel } from "./panels/selected-factor-panel"
import { useFactorLabPage } from "./hooks/use-factor-lab-page"
import { useMemo, useState } from "react"
import {
  championFromFavorite,
  championFromHistory,
} from "./hooks/factor-helpers"

/** 已收藏 tokens_key 集合（用于 Champion 表/详情/历史置灰） */
function buildFavoritedKeys(
  favorites: { tokens: number[] }[],
): Set<string> {
  const set = new Set<string>()
  for (const f of favorites) {
    if (f.tokens?.length) set.add(f.tokens.join(","))
  }
  return set
}

/** 因子实验室（client） */
export function FactorLabPage(): React.JSX.Element {
  const s = useFactorLabPage()
  const favoritedKeys = useMemo(
    () => buildFavoritedKeys(s.favorites),
    [s.favorites],
  )
  // 收藏走弹窗：选文件夹（一级菜单）归类后再保存
  const [favPending, setFavPending] = useState<FavoriteInput | null>(null)

  function championToFavoriteInput(c: {
    tokens: number[]
    text?: string
    composite?: number
    metrics?: unknown
  }): FavoriteInput {
    return {
      tokens: c.tokens,
      text: c.text ?? "",
      symbol: s.lastReq?.symbol ?? s.symbol,
      timeframe: s.lastReq?.timeframe ?? s.result?.timeframe ?? "",
      composite: Number(c.composite ?? 0),
      metrics: (c.metrics ?? {}) as FavoriteInput["metrics"],
      // metrics 宽松传入（Champion 为 FactorMetrics，结构兼容 Partial<FactorMetrics>）
    }
  }

  return (
    <div className="h-full overflow-y-auto p-4 md:p-6 space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-[var(--text-primary)]">
          因子实验室
        </h1>
        <p className="text-xs text-[var(--text-muted)] mt-1">
          遗传规划挖因子；可选 LLM 教练进化（路线 A）或 LLM 直接生成（路线
          B）。结果自动进历史，可收藏后在 AI 交易选用。
        </p>
      </div>

      <FactorLabGuide />

      <FactorSearchForm
        defaultSymbol={s.symbol}
        loading={s.loading}
        onSearch={(p) => void s.handleSearch(p)}
        onSymbolChange={s.setSymbol}
      />

      {s.error && (
        <div className="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-md px-3 py-2">
          {s.error}
        </div>
      )}

      {s.result && (
        <EvolveBar
          disabled={!s.result.champions.length}
          loading={s.loading}
          coachNote={s.result.coach_note ?? null}
          appliedSource={s.result.applied_config?.source ?? null}
          onEvolve={() => void s.handleEvolve()}
        />
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="space-y-2">
          <h2 className="text-sm font-medium text-[var(--text-secondary)] flex items-center gap-1">
            Champion 排行
            <HelpTip
              text={FACTOR_HELP.champion}
              side="bottom"
              align="center"
              className=""
            />
          </h2>
          {s.result && s.result.timeframe !== "1d" && (
            <div className="text-[11px] text-down">
              ⚠ 分钟级冠军的保守 OOS 普遍为负（7 品种 × 4 周期实测），
              慎挂实盘——详见 oos_conservative 与 session 指标
            </div>
          )}
          <ChampionTable
            champions={s.result?.champions ?? []}
            selectedTokens={s.selected?.tokens ?? null}
            onSelect={(c) => void s.selectFactor(c)}
            onFavorite={(c) => setFavPending(championToFavoriteInput(c))}
            favoritedKeys={favoritedKeys}
            onComboMount={(cs) => void s.handleComboMount(cs)}
          />
        </div>
        <SelectedFactorPanel
          selected={s.selected}
          bt={s.bt}
          btLoading={s.btLoading}
          building={s.building}
          buildMsg={s.buildMsg}
          favoritedKeys={favoritedKeys}
          onBuildTask={() => void s.handleBuildTask()}
          onFavorite={() => {
            if (s.selected) setFavPending(championToFavoriteInput(s.selected))
          }}
          onCopyTokens={() => {
            if (s.selected && navigator.clipboard) {
              void navigator.clipboard.writeText(s.selected.tokens.join(","))
              s.setBuildMsg("tokens 已复制")
            }
          }}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <HistoryPanel
          symbol={s.symbol}
          items={s.history}
          loading={s.histLoading}
          favoritedKeys={favoritedKeys}
          onSelect={(it) => {
            const c = championFromHistory(it)
            const base: SearchFormPayload = s.lastReq ?? {
              symbol: it.symbol,
              timeframe: it.timeframe,
              population: 30,
              generations: 15,
              top_n: 10,
              seed: 42,
              cost: null,
              use_llm_coach: false,
              model_row_id: null,
            }
            void s.selectFactor(c, {
              ...base,
              symbol: it.symbol,
              timeframe: it.timeframe,
            })
          }}
          onDelete={(id) => void s.deleteHistory(id)}
          onFavorite={(it) => setFavPending(it)}
          onRefresh={() => void s.refreshHistory(s.symbol)}
        />
        <FavoritesPanel
          items={s.favorites}
          loading={s.favLoading}
          onSelect={(it) => {
            void s.selectFactor(championFromFavorite(it), {
              symbol: it.symbol || s.symbol,
              timeframe: it.timeframe || s.lastReq?.timeframe || "1d",
              population: 30,
              generations: 15,
              top_n: 10,
              seed: 42,
              cost: null,
              use_llm_coach: false,
              model_row_id: null,
            })
          }}
          onDelete={(id) => void s.deleteFavorite(id)}
          onRefresh={() => void s.refreshFavorites(s.symbol)}
        />
        <GeneratePanel
          models={s.models}
          modelsLoading={s.modelsLoading}
          loading={s.genLoading}
          onGenerate={(p) => void s.handleGenerate(p)}
        />
      </div>

      {s.result?.portfolio && (
        <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)]/30 px-3 py-2 text-[11px] text-[var(--text-secondary)] font-num flex items-center gap-3 flex-wrap">
          <span className="text-[var(--text-muted)]">
            冠军组合（{s.result.portfolio.n_factors} 因子 · 平均|相关性|{" "}
            {s.result.portfolio.avg_abs_corr}）
          </span>
          <span>
            等权 年化{" "}
            <span
              className={
                s.result.portfolio.equal.ann_ret >= 0 ? "text-up" : "text-down"
              }
            >
              {(s.result.portfolio.equal.ann_ret * 100).toFixed(1)}%
            </span>{" "}
            · Sortino {s.result.portfolio.equal.sortino.toFixed(2)}
          </span>
          {s.result.portfolio.ic_weighted && (
            <span>
              IC 加权 年化{" "}
              <span
                className={
                  s.result.portfolio.ic_weighted.ann_ret >= 0
                    ? "text-up"
                    : "text-down"
                }
              >
                {(s.result.portfolio.ic_weighted.ann_ret * 100).toFixed(1)}%
              </span>{" "}
              · Sortino {s.result.portfolio.ic_weighted.sortino.toFixed(2)}
            </span>
          )}
          <span className="text-[var(--text-muted)]">
            （最优单因子 Sortino{" "}
            {s.result.portfolio.best_single.sortino.toFixed(2)}）
          </span>
        </div>
      )}
      {s.result && (
        <div className="text-[11px] text-[var(--text-muted)]">
          基于 {s.result.bars} 根 {s.result.timeframe} K 线 · 区间{" "}
          {s.result.range.from} ~ {s.result.range.to}
          {s.result.applied_config && (
            <>
              {" "}
              · 种群 {s.result.applied_config.population} · 代数{" "}
              {s.result.applied_config.generations}
              {s.result.applied_config.periods != null && (
                <> · 年化基数 {s.result.applied_config.periods} 根/年</>
              )}
              {s.result.applied_config.cost != null && (
                <>
                  {" "}
                  · 单边成本{" "}
                  {(s.result.applied_config.cost * 10000).toFixed(2)}bp
                  {s.result.applied_config.cost_auto === true ? "（按品种）" : ""}
                </>
              )}
            </>
          )}
        </div>
      )}
      <FactorFavoriteSaveDialog
        item={favPending}
        defaultName={favPending ? s.defaultFavoriteName(favPending) : ""}
        onClose={() => setFavPending(null)}
        onSave={(item, opts) => s.saveFavorite(item, opts)}
      />
    </div>
  )
}
