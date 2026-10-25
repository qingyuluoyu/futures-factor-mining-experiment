"use client"

import type { AuthMode } from "./auth/auth-dialog"

interface LandingHeroProps {
  runningCount: number
  totalPnl: number
  openPositionCount: number
  updatedAt: string | null
  onOpenAuth: (mode: AuthMode) => void
}

function formatPnl(v: number): string {
  const sign = v > 0 ? "+" : ""
  return `${sign}${v.toFixed(2)}`
}

/** 首页 Hero：标题 + 实时指标条 */
export function LandingHero({
  runningCount,
  totalPnl,
  openPositionCount,
  updatedAt,
  onOpenAuth,
}: LandingHeroProps): React.JSX.Element {
  const timeLabel = updatedAt
    ? new Date(updatedAt).toLocaleTimeString("zh-CN", { hour12: false })
    : "--:--:--"

  return (
    <section className="relative pt-10 md:pt-14 pb-6 md:pb-8">
      <div className="mx-auto max-w-7xl px-4 md:px-6">
        <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-6">
          <div className="max-w-2xl">
            <div className="inline-flex items-center gap-2 rounded-full border border-red-500/25 bg-red-500/10 px-3 py-1 text-[11px] text-[var(--accent-up)] mb-4">
              <span className="relative flex h-1.5 w-1.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-60" />
                <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-red-400" />
              </span>
              实盘级 AI 交易终端 · 7×24 虚拟盘
            </div>
            <h1 className="text-3xl md:text-4xl lg:text-[2.75rem] font-semibold tracking-tight text-[var(--text-primary)] leading-[1.15]">
              看清每一笔
              <span className="text-transparent bg-clip-text bg-gradient-to-r from-red-400 via-red-300 to-amber-200">
                {" "}
                AI 决策
              </span>
              <br className="hidden sm:block" />
              的实时收益轨迹
            </h1>
            <p className="mt-4 text-sm md:text-[15px] text-[var(--text-secondary)] leading-relaxed max-w-xl">
              多模型同场竞技，资金曲线与任务总收益横向对比。登录后即可创建 AI /
              量化任务，接入实盘或虚拟盘行情。
            </p>
            <div className="mt-6 flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={() => onOpenAuth("login")}
                className="inline-flex items-center justify-center rounded-lg bg-gradient-to-r from-red-600 to-red-500 px-5 py-2.5 text-sm font-medium text-white shadow-[0_0_24px_rgba(239,68,68,0.35)] hover:from-red-500 hover:to-red-400 transition-colors duration-200"
              >
                立即登录交易
              </button>
              <button
                type="button"
                onClick={() => onOpenAuth("register")}
                className="inline-flex items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] px-5 py-2.5 text-sm text-[var(--text-primary)] hover:border-red-400/40 hover:bg-red-500/10 transition-colors duration-200"
              >
                免费注册
              </button>
              <a
                href="#live"
                className="inline-flex items-center justify-center rounded-lg border border-[var(--border)] px-5 py-2.5 text-sm text-[var(--text-secondary)] hover:border-[var(--text-muted)] transition-colors duration-200"
              >
                查看实时看板
              </a>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-2 sm:gap-3 w-full lg:w-auto lg:min-w-[340px]">
            <StatCard label="运行任务" value={String(runningCount)} accent="text-red-300" />
            <StatCard
              label="展示总收益"
              value={formatPnl(totalPnl)}
              accent={totalPnl >= 0 ? "text-up" : "text-down"}
            />
            <StatCard
              label="持仓任务"
              value={String(openPositionCount)}
              accent="text-amber-200"
              sub={`更新 ${timeLabel}`}
            />
          </div>
        </div>
      </div>
    </section>
  )
}

function StatCard(props: {
  label: string
  value: string
  accent: string
  sub?: string
}): React.JSX.Element {
  const { label, value, accent, sub } = props
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-tertiary)] backdrop-blur-md px-3 py-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
      <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
        {label}
      </p>
      <p className={`mt-1 text-lg sm:text-xl font-num font-semibold ${accent}`}>
        {value}
      </p>
      {sub ? (
        <p className="mt-0.5 text-[10px] text-[var(--text-muted)]">{sub}</p>
      ) : (
        <p className="mt-0.5 text-[10px] text-transparent">.</p>
      )}
    </div>
  )
}
