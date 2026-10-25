"use client"

import Link from "next/link"
import { useAuthStore } from "@/stores/auth"
import type { AuthMode } from "./auth/auth-dialog"
import { BrandLogo } from "@/components/common/brand-logo"
import { BRAND_NAME, BRAND_TAGLINE } from "@/lib/brand"

interface LandingHeaderProps {
  onOpenAuth: (mode: AuthMode) => void
}

/** 首页顶栏：品牌 + 右上角登录/进入系统 */
export function LandingHeader({
  onOpenAuth,
}: LandingHeaderProps): React.JSX.Element {
  const user = useAuthStore((s) => s.user)
  const accessToken = useAuthStore((s) => s.accessToken)
  const loggedIn = Boolean(user && accessToken)

  return (
    <header className="sticky top-0 z-40 border-b border-[var(--border)] bg-[var(--bg-primary)]/75 backdrop-blur-xl">
      <div className="mx-auto max-w-7xl px-4 md:px-6 h-14 flex items-center justify-between gap-3">
        <Link href="/" className="flex items-center gap-2.5 group">
          <BrandLogo decorative />
          <div className="leading-tight">
            <p className="text-sm font-semibold text-[var(--text-primary)] tracking-wide group-hover:text-[var(--text-primary)]">
              {BRAND_NAME}
            </p>
            <p className="text-[10px] text-[var(--text-muted)] hidden sm:block">
              {BRAND_TAGLINE}
            </p>
          </div>
        </Link>

        <nav className="hidden md:flex items-center gap-6 text-xs text-[var(--text-secondary)]">
          <a href="#live" className="hover:text-red-300 transition-colors duration-200">
            实时看板
          </a>
          <a href="#showcase" className="hover:text-red-300 transition-colors duration-200">
            功能展示
          </a>
          <a href="#features" className="hover:text-red-300 transition-colors duration-200">
            产品能力
          </a>
          <a href="#workflow" className="hover:text-red-300 transition-colors duration-200">
            交易闭环
          </a>
        </nav>

        <div className="flex items-center gap-2">
          {loggedIn ? (
            <>
              <span className="hidden sm:inline text-[11px] text-[var(--text-muted)] max-w-[120px] truncate">
                {user?.username}
              </span>
              <Link
                href="/ai-trading"
                className="inline-flex items-center gap-1.5 rounded-full border border-red-500/40 bg-red-500/15 px-3.5 py-1.5 text-xs font-medium text-[var(--accent-up)] hover:bg-red-500/25 hover:border-red-400/60 shadow-[0_0_16px_rgba(239,68,68,0.15)] transition-colors duration-200"
              >
                进入系统
              </Link>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={() => onOpenAuth("register")}
                className="hidden sm:inline-flex items-center rounded-full border border-[var(--border)] px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:border-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors duration-200"
              >
                注册
              </button>
              <button
                type="button"
                onClick={() => onOpenAuth("login")}
                className="inline-flex items-center gap-1.5 rounded-full border border-red-500/40 bg-red-500/15 px-3.5 py-1.5 text-xs font-medium text-[var(--accent-up)] hover:bg-red-500/25 hover:border-red-400/60 shadow-[0_0_16px_rgba(239,68,68,0.15)] transition-colors duration-200"
              >
                登录
              </button>
            </>
          )}
        </div>
      </div>
    </header>
  )
}
