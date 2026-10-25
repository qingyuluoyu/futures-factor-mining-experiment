"use client"

/**
 * 营销首页 —— 科技网格 + 实时 AI 收益看板 + 产品介绍
 * 登录/注册在首页以弹窗面板展示
 */

import { useCallback, useEffect, useState } from "react"
import { LandingFeatures } from "@/components/landing/landing-features"
import { LandingHeader } from "@/components/landing/landing-header"
import { LandingHero } from "@/components/landing/landing-hero"
import { LandingLiveBoard } from "@/components/landing/landing-live-board"
import { LandingShowcase } from "@/components/landing/landing-showcase"
import { TechGridBg } from "@/components/landing/tech-grid-bg"
import { BRAND_NAME } from "@/lib/brand"
import { AuthDialog, type AuthMode } from "@/components/landing/auth/auth-dialog"
import {
  fetchShowcase,
  type ShowcasePayload,
} from "@/lib/showcase-api"

const POLL_MS = 5000
const LIMIT = 12

/** 首页 */
export default function HomePage(): React.JSX.Element {
  const [mode, setMode] = useState<"all" | "live" | "virtual">("all")
  const [data, setData] = useState<ShowcasePayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [authOpen, setAuthOpen] = useState(false)
  const [authMode, setAuthMode] = useState<AuthMode>("login")

  const openAuth = useCallback((m: AuthMode) => {
    setAuthMode(m)
    setAuthOpen(true)
  }, [])

  const load = useCallback(async () => {
    try {
      const payload = await fetchShowcase(
        LIMIT,
        mode === "all" ? null : mode,
      )
      setData(payload)
      setError(null)
      setLoading(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败")
      setLoading(false)
    }
  }, [mode])

  useEffect(() => {
    setLoading(true)
    void load()
    const timer = setInterval(() => {
      void load()
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [load])

  return (
    <div className="relative min-h-screen text-[var(--text-primary)]">
      <TechGridBg />
      <div className="relative z-10 flex min-h-screen flex-col">
        <LandingHeader onOpenAuth={openAuth} />
        <main className="flex-1">
          <LandingHero
            runningCount={data?.running_count ?? 0}
            totalPnl={data?.total_pnl ?? 0}
            openPositionCount={data?.open_position_count ?? 0}
            updatedAt={data?.updated_at ?? null}
            onOpenAuth={openAuth}
          />
          <LandingLiveBoard
            data={data}
            loading={loading}
            error={error}
            mode={mode}
            onModeChange={setMode}
            pollSeconds={POLL_MS / 1000}
          />
          <LandingShowcase />
          <LandingFeatures />
        </main>
        <footer className="relative z-10 border-t border-[var(--border)] py-6">
          <div className="mx-auto max-w-7xl px-4 md:px-6 flex flex-col sm:flex-row items-center justify-between gap-2 text-[11px] text-[var(--text-muted)]">
            <span>{BRAND_NAME} · 研究与模拟交易工作台</span>
            <span className="font-num opacity-70">
              Live · Risk · AI Decision
            </span>
          </div>
        </footer>
      </div>

      <AuthDialog
        open={authOpen}
        initialMode={authMode}
        onClose={() => setAuthOpen(false)}
      />
    </div>
  )
}
