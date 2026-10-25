"use client"

import { useEffect } from "react"
import { useRouter, usePathname } from "next/navigation"
import { useAuthStore } from "@/stores/auth"
import { getBoundSessionApi } from "@/lib/api"
import { loadSessionOnce, sessionStillCurrent } from "@/lib/auth-session"

/** 需要认证才能访问的路径前缀 */
const PROTECTED_PREFIXES = [
  "/dashboard",
  "/market",
  "/trading",
  "/positions",
  "/orders",
  "/assets",
  "/history",
  "/profile",
  "/messages",
  "/admin",
  "/ai-trading",
  "/ai-settings",
  "/ai",
  "/backtest",
  "/factor-lab",
  "/factor-research",
]

/** 无需认证的路径 */
const PUBLIC_PATHS = ["/login", "/register", "/"]

/**
 * 鉴权 + 会话恢复
 * 有 token 但内存/缓存用户缺失时，自动拉 /api/auth/me，避免刷新后角色丢失
 */
export function useAuthGuard(): void {
  const router = useRouter()
  const pathname = usePathname()
  const accessToken = useAuthStore((s) => s.accessToken)
  const user = useAuthStore((s) => s.user)
  const loaded = useAuthStore((s) => s.loaded)
  const login = useAuthStore((s) => s.login)
  const logout = useAuthStore((s) => s.logout)
  const setLoaded = useAuthStore((s) => s.setLoaded)
  const setHydrating = useAuthStore((s) => s.setHydrating)
  const restoreSessionToken = useAuthStore((s) => s.restoreSessionToken)

  // 会话恢复：token 在、需要确认用户时拉 /me
  useEffect(() => {
    let cancelled = false

    async function hydrate(): Promise<void> {
      const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : accessToken

      if (!token) {
        if (accessToken) {
          logout()
          return
        }
        if (!cancelled) {
          setLoaded(true)
          setHydrating(false)
        }
        return
      }
      if (token !== accessToken) {
        restoreSessionToken(token)
        return
      }
      const refresh = localStorage.getItem("refresh_token") ?? ""
      const current = (): boolean => !cancelled && sessionStillCurrent(token, useAuthStore.getState().accessToken, localStorage.getItem("access_token"))

      // 已有用户且已 loaded：仍周期性不强制；首次进入必须校验一次
      if (user && loaded) {
        return
      }

      setHydrating(true)
      try {
        if (!current()) return
        const restored = await loadSessionOnce(token, () => getBoundSessionApi(token, refresh))
        if (!current()) return
        login(restored.user, restored.accessToken, restored.refreshToken)
      } catch {
        if (!current()) return
        // token 无效
        logout()
      } finally {
        if (current()) {
          setHydrating(false)
          setLoaded(true)
        } else if (!cancelled && useAuthStore.getState().accessToken === token) {
          const replacement = localStorage.getItem("access_token")
          if (replacement) restoreSessionToken(replacement)
          else logout()
        }
      }
    }

    void hydrate()
    return () => {
      cancelled = true
    }
  }, [
    accessToken,
    user,
    loaded,
    login,
    logout,
    setLoaded,
    setHydrating,
    restoreSessionToken,
  ])

  // 路由守卫
  useEffect(() => {
    if (!loaded) return

    const isProtected = PROTECTED_PREFIXES.some((prefix) =>
      pathname.startsWith(prefix),
    )
    const isPublic = PUBLIC_PATHS.includes(pathname)

    if (isProtected && !accessToken) {
      router.replace("/login")
      return
    }
    if (isPublic && pathname === "/" && accessToken) {
      router.replace("/dashboard")
    }
  }, [pathname, accessToken, loaded, router])
}
