"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { useAuthStore } from "@/stores/auth"
import { loginApi } from "@/lib/api"
import { establishLoginSession } from "@/lib/login-session"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { BrandLogo } from "@/components/common/brand-logo"
import { BRAND_NAME, BRAND_TAGLINE } from "@/lib/brand"
import { loadRememberedAccount, saveRememberedAccount } from "@/lib/remembered-account"
import { LoginDashboardBackground } from "@/components/landing/auth/login-dashboard-background"

/** 登录页面 */
export default function LoginPage(): React.JSX.Element {
  const router = useRouter()
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [tradingMode, setTradingMode] = useState<"live" | "virtual">("live")
  const [remember, setRemember] = useState(false)
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)
  const [hydrated, setHydrated] = useState(false)

  useEffect(() => {
    const saved = loadRememberedAccount()
    setUsername(saved.username)
    setRemember(saved.remember)
    setHydrated(true)
  }, [])

  async function handleSubmit(e: React.FormEvent): Promise<void> {
    e.preventDefault()
    setError("")
    setLoading(true)
    const revision = useAuthStore.getState().sessionRevision

    try {
      const authRes = await loginApi({
        account: username,
        password,
        trading_mode: tradingMode,
      })
      if (!await establishLoginSession(authRes, revision)) return
      saveRememberedAccount(remember, username)
      router.push("/dashboard")
    } catch (err) {
      setError(err instanceof Error ? err.message : "账号或密码错误")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 isolate overflow-hidden bg-[var(--bg-primary)]">
      <LoginDashboardBackground />
      <div className="absolute inset-0 z-10 flex flex-col overflow-y-auto overscroll-contain bg-[var(--bg-primary)]/15 px-4 py-28 md:py-12">
      <div className="m-auto w-full max-w-md shrink-0 rounded-2xl border border-[var(--border)] bg-[var(--bg-secondary)]/80 px-6 py-8 shadow-2xl backdrop-blur-md sm:px-8 sm:py-10">
        <div className="flex flex-col items-center mb-8">
          <BrandLogo size={56} decorative className="mb-4" />
          <h1 className="text-xl font-semibold text-[var(--text-primary)]">
            {BRAND_NAME}
          </h1>
          <p className="text-sm text-[var(--text-muted)] mt-1">
            {BRAND_TAGLINE}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-5">
          {error && (
            <div className="px-4 py-2.5 rounded-md bg-[var(--accent-danger)]/15 text-[var(--accent-danger)] text-sm text-center">
              {error}
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="username">用户名</Label>
            <Input
              id="username"
              placeholder="请输入用户名"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="password">密码</Label>
            <Input
              id="password"
              type="password"
              placeholder="请输入密码"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={remember ? "current-password" : "off"}
              required
            />
          </div>

          <div className="space-y-2">
            <Label>数据盘（登录后不可切换）</Label>
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => setTradingMode("live")}
                className={`h-10 rounded-md border text-sm transition-colors ${
                  tradingMode === "live"
                    ? "border-[var(--primary)] bg-[var(--primary)]/15 text-[var(--primary)]"
                    : "border-[var(--border)] text-[var(--text-secondary)]"
                }`}
              >
                市场行情模拟
              </button>
              <button
                type="button"
                onClick={() => setTradingMode("virtual")}
                className={`h-10 rounded-md border text-sm transition-colors ${
                  tradingMode === "virtual"
                    ? "border-[var(--accent-warn)] bg-[var(--accent-warn)]/15 text-[var(--accent-warn)]"
                    : "border-[var(--border)] text-[var(--text-secondary)]"
                }`}
              >
                7×24 模拟
              </button>
            </div>
            <p className="text-[11px] text-[var(--text-muted)] leading-relaxed">
              {tradingMode === "live"
                ? "使用市场行情和模拟资金，不连接真实资金账户。"
                : "使用 7×24 行情和独立模拟资金、持仓、任务；不连接真实资金账户。"}
            </p>
          </div>

          <div className="flex items-center justify-between">
            <label
              htmlFor="remember"
              className="flex items-center gap-2 text-sm text-[var(--text-secondary)] cursor-pointer select-none"
            >
              <input
                id="remember"
                type="checkbox"
                checked={remember}
                onChange={(e) => {
                  const next = e.target.checked
                  setRemember(next)
                  // 取消勾选时立即清掉本地保存
                  if (!next) {
                    saveRememberedAccount(false, "")
                  }
                }}
                className="w-4 h-4 rounded border-[var(--border)] bg-[var(--bg-primary)] accent-[var(--primary)]"
              />
              记住用户名
            </label>
            <span className="text-xs text-[var(--text-muted)]">
              登录有效期 7 天
            </span>
          </div>

          <Button
            type="submit"
            className="w-full"
            disabled={loading || !hydrated}
          >
            {loading ? "登录中..." : "登录"}
          </Button>
        </form>

        <div className="flex items-center justify-between mt-6 text-sm">
          <Link
            href="/register"
            className="text-[var(--primary)] hover:underline"
          >
            注册账号
          </Link>
          <button
            type="button"
            className="text-[var(--text-muted)] hover:text-[var(--text-secondary)] cursor-pointer"
          >
            忘记密码？
          </button>
        </div>
      </div>
      </div>
    </div>
  )
}
