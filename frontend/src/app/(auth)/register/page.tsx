"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { useAuthStore } from "@/stores/auth"
import { registerApi, getBoundSessionApi } from "@/lib/api"
import {
  getRegistrationStatusApi,
  type RegistrationStatus,
} from "@/lib/admin-api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { BrandLogo } from "@/components/common/brand-logo"
import { BRAND_NAME, BRAND_TAGLINE } from "@/lib/brand"

/** 注册页面 —— 对接真实注册 + 注册策略提示 */
export default function RegisterPage(): React.JSX.Element {
  const router = useRouter()
  const { login } = useAuthStore()
  const [form, setForm] = useState({
    username: "",
    phone: "",
    password: "",
    confirmPassword: "",
  })
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)
  const [createdAccount, setCreatedAccount] = useState(false)
  const [regStatus, setRegStatus] = useState<RegistrationStatus | null>(null)
  const [statusLoading, setStatusLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    async function load(): Promise<void> {
      try {
        const registrationStatus = await getRegistrationStatusApi()
        if (!cancelled) setRegStatus(registrationStatus)
      } catch {
        if (!cancelled) {
          setRegStatus(null)
        }
      } finally {
        if (!cancelled) setStatusLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [])

  function updateField(field: string, value: string): void {
    setForm((prev) => ({ ...prev, [field]: value }))
  }

  async function handleSubmit(e: React.FormEvent): Promise<void> {
    e.preventDefault()
    setError("")

    if (createdAccount) {
      setError("账号已创建，请直接登录")
      return
    }
    if (regStatus && !regStatus.can_register) {
      setError(regStatus.reason || "当前不可注册")
      return
    }
    if (form.password !== form.confirmPassword) {
      setError("两次密码输入不一致")
      return
    }
    if (form.username.length < 3 || form.username.length > 50) {
      setError("用户名须为 3 至 50 个字符")
      return
    }
    if (form.password.length < 6) {
      setError("密码至少 6 个字符")
      return
    }
    if (!form.password.trim() || form.password.includes("\0")) {
      setError("密码不能全为空白或包含空字符")
      return
    }
    if (new TextEncoder().encode(form.password).length > 72) {
      setError("密码 UTF-8 编码长度不能超过 72 字节")
      return
    }

    setLoading(true)
    let registrationSucceeded = false
    try {
      const authRes = await registerApi({
        username: form.username,
        password: form.password,
        phone: form.phone.trim() || null,
      })
      registrationSucceeded = true
      setCreatedAccount(true)
      const session = await getBoundSessionApi(authRes.access_token, authRes.refresh_token)
      login(session.user, session.accessToken, session.refreshToken)
      router.push("/dashboard")
    } catch (err) {
      setError(registrationSucceeded
        ? "账号已创建，自动登录失败，请使用刚创建的账号前往登录"
        : err instanceof Error ? err.message : "注册失败，请重试")
    } finally {
      setLoading(false)
    }
  }

  const blocked = Boolean(regStatus && !regStatus.can_register)

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-[var(--bg-primary)] via-[var(--bg-secondary)] to-[var(--bg-primary)]">
      <div className="w-full max-w-md px-8 py-10 rounded-xl border border-[var(--border)] bg-[var(--bg-secondary)] shadow-2xl">
        <div className="flex flex-col items-center mb-8">
          <BrandLogo size={56} decorative className="mb-4" />
          <h1 className="text-xl font-semibold text-[var(--text-primary)]">
            注册{BRAND_NAME}
          </h1>
          <p className="text-xs text-[var(--text-muted)] mt-1">
            {BRAND_TAGLINE}
          </p>
        </div>

        {statusLoading && (
          <p className="text-sm text-center text-[var(--text-muted)] mb-4">
            正在检查注册状态…
          </p>
        )}

        {!statusLoading && regStatus && (
          <div
            className={`mb-4 px-4 py-2.5 rounded-md text-sm text-center ${
              blocked
                ? "bg-[var(--accent-danger)]/15 text-[var(--accent-danger)]"
                : "bg-[var(--primary)]/10 text-[var(--text-secondary)]"
            }`}
          >
            {blocked ? (
              <span>{regStatus.reason || "当前不可注册"}</span>
            ) : (
              <span>
                开放注册
                {regStatus.registration_policy === "open" && "，无每日名额限制"}
                {regStatus.registration_policy !== "open" && regStatus.daily_register_limit > 0 && (
                  <>
                    ，今日剩余{" "}
                    <strong className="text-[var(--text-primary)]">
                      {regStatus.remaining ?? 0}
                    </strong>{" "}
                    / {regStatus.daily_register_limit} 名额
                    （每日 {regStatus.register_reset_hour}:00 重置）
                  </>
                )}
              </span>
            )}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          {error && (
            <div className="px-4 py-2.5 rounded-md bg-[var(--accent-danger)]/15 text-[var(--accent-danger)] text-sm text-center">
              {error}
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="username">用户名</Label>
            <Input
              id="username"
              placeholder="字母/数字/下划线，至少 3 位"
              minLength={3}
              maxLength={50}
              autoComplete="username"
              value={form.username}
              onChange={(e) => updateField("username", e.target.value)}
              required
              disabled={blocked}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="phone">手机号（可选）</Label>
            <Input
              id="phone"
              placeholder="请输入手机号"
              type="tel"
              maxLength={20}
              autoComplete="tel"
              value={form.phone}
              onChange={(e) => updateField("phone", e.target.value)}
              disabled={blocked}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="password">密码</Label>
            <Input
              id="password"
              type="password"
              placeholder="至少 6 位"
              minLength={6}
              maxLength={72}
              autoComplete="new-password"
              value={form.password}
              onChange={(e) => updateField("password", e.target.value)}
              required
              disabled={blocked}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="confirmPassword">确认密码</Label>
            <Input
              id="confirmPassword"
              type="password"
              placeholder="请再次输入密码"
              minLength={6}
              maxLength={72}
              autoComplete="new-password"
              value={form.confirmPassword}
              onChange={(e) => updateField("confirmPassword", e.target.value)}
              required
              disabled={blocked}
            />
          </div>

          <Button
            type="submit"
            className="w-full"
            disabled={loading || blocked || statusLoading || createdAccount}
          >
            {loading ? "注册中..." : createdAccount ? "账号已创建，请登录" : blocked ? "暂不可注册" : "注册"}
          </Button>
        </form>

        <div className="text-center mt-6 text-sm">
          <span className="text-[var(--text-muted)]">已有账号？</span>{" "}
          <Link href="/login" className="text-[var(--primary)] hover:underline">
            立即登录
          </Link>
        </div>
      </div>
    </div>
  )
}
