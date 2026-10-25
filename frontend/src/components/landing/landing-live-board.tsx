"use client"

/**
 * 首页实时看板：AI 收益对比 + 任务总收益横向柱
 */

import { EquityChart } from "@/components/ai-trading/equity/equity-chart"
import { ProfitBarChart } from "@/components/ai-trading/profit/profit-bar-chart"
import type { ShowcasePayload } from "@/lib/showcase-api"

interface LandingLiveBoardProps {
  data: ShowcasePayload | null
  loading: boolean
  error: string | null
  mode: "all" | "live" | "virtual"
  onModeChange: (mode: "all" | "live" | "virtual") => void
  pollSeconds: number
}

/** 动态 AI 收益展示区（数据由父级轮询注入） */
export function LandingLiveBoard({
  data,
  loading,
  error,
  mode,
  onModeChange,
  pollSeconds,
}: LandingLiveBoardProps): React.JSX.Element {
  const tasks = data?.tasks ?? []
  const series = data?.equity_series ?? {}
  const profitItems = data?.profit_items ?? []

  return (
    <section id="live" className="relative pb-10 md:pb-14">
      <div className="mx-auto max-w-7xl px-4 md:px-6 space-y-4">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div>
            <p className="text-[11px] tracking-[0.2em] uppercase text-red-400/90 mb-1">
              Live Board
            </p>
            <h2 className="text-xl md:text-2xl font-semibold text-[var(--text-primary)]">
              正在运行的 AI 模型收益
            </h2>
            <p className="text-xs text-[var(--text-muted)] mt-1">
              每 {pollSeconds}s 自动刷新 · 脱敏公开展示 · 登录后可创建自己的任务
            </p>
          </div>
          <div className="flex items-center gap-1 rounded-full border border-[var(--border)] bg-[var(--bg-tertiary)] p-1">
            {(
              [
                ["all", "全部"],
                ["live", "实盘"],
                ["virtual", "虚拟盘"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => onModeChange(key)}
                className={`px-3 py-1 rounded-full text-[11px] transition-colors duration-200 ${
                  mode === key
                    ? "bg-red-500/25 text-[var(--accent-up)] border border-red-500/40"
                    : "text-[var(--text-muted)] hover:text-[var(--text-primary)] border border-transparent"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {error && (
          <div className="text-xs text-amber-300/90 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">
            {error}（将继续重试）
          </div>
        )}

        <div className="rounded-2xl p-px bg-gradient-to-b from-red-500/30 via-white/10 to-transparent">
          <div className="rounded-2xl bg-[var(--bg-secondary)]/90 backdrop-blur-sm overflow-hidden">
            <EquityChart tasks={tasks} series={series} profitBars={profitItems} />
          </div>
        </div>

        <div className="rounded-2xl p-px bg-gradient-to-b from-red-500/25 via-white/10 to-transparent">
          <div className="rounded-2xl bg-[var(--bg-secondary)]/90 backdrop-blur-sm overflow-hidden">
            <ProfitBarChart
              items={profitItems}
              totalRealized={data?.total_realized ?? 0}
              totalUnrealized={data?.total_unrealized ?? 0}
              totalPnl={data?.total_pnl ?? 0}
              openPositionCount={data?.open_position_count ?? 0}
              loading={loading}
            />
          </div>
        </div>
      </div>
    </section>
  )
}
