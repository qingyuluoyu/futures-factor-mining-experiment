"use client"

import { create } from "zustand"
import { switchMarketApi } from "@/lib/api"
import { getMarketWebSocket } from "@/lib/websocket"
import { useNotificationsStore } from "@/stores/notifications"
import type { User } from "@/types"
import { beginSessionRestoreState } from "@/lib/auth-session"

const ACCESS_KEY = "access_token"
const REFRESH_KEY = "refresh_token"
const USER_KEY = "qihuo_auth_user"

/** 从 localStorage 恢复用户（刷新后保持角色） */
function readCachedUser(): User | null {
  if (typeof window === "undefined") return null
  try {
    const raw = localStorage.getItem(USER_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as User
    if (!parsed?.id || !parsed?.username) return null
    // 角色只认 admin / user，避免脏数据
    const role = parsed.role === "admin" ? "admin" : "user"
    const trading_mode =
      parsed.trading_mode === "virtual" ? "virtual" : "live"
    const market_type =
      parsed.market_type === "stock" ? "stock" : "futures"
    return { ...parsed, role, trading_mode, market_type }
  } catch {
    return null
  }
}

function writeCachedUser(user: User | null): void {
  if (typeof window === "undefined") return
  try {
    if (user) {
      localStorage.setItem(USER_KEY, JSON.stringify(user))
    } else {
      localStorage.removeItem(USER_KEY)
    }
  } catch {
    // 隐私模式等写失败忽略
  }
}

interface AuthState {
  sessionRevision: number
  marketSwitchError: string | null
  marketSwitchPending: boolean
  /** 当前用户 */
  user: User | null
  /** JWT access token */
  accessToken: string | null
  /** 是否已完成会话恢复（含 /me 校验） */
  loaded: boolean
  /** 是否正在拉取 /me */
  hydrating: boolean
  /** 登录 */
  login: (user: User, accessToken: string, refreshToken: string) => void
  /** 登出 */
  logout: () => void
  /** 更新用户信息 */
  updateUser: (user: Partial<User>) => void
  /** 切换市场偏好（futures=期货 / stock=A股），持久化到后端 */
  switchMarket: (market: "futures" | "stock") => Promise<void>
  /** 设置 access token */
  setAccessToken: (token: string) => void
  restoreSessionToken: (token: string) => void
  /** 标记会话恢复完成 */
  setLoaded: (loaded: boolean) => void
  /** 标记 hydrating */
  setHydrating: (hydrating: boolean) => void
}

/** 认证状态管理 —— 用户信息本地缓存，刷新后不丢角色 */
export const useAuthStore = create<AuthState>((set, get) => {
  let marketQueue: Promise<void> = Promise.resolve()
  let queuedSession = -1
  let marketSequence = 0
  let confirmedMarket: "futures" | "stock" = "futures"
  return {
  sessionRevision: 0,
  marketSwitchError: null,
  marketSwitchPending: false,
  user: typeof window !== "undefined" ? readCachedUser() : null,
  accessToken:
    typeof window !== "undefined" ? localStorage.getItem(ACCESS_KEY) : null,
  loaded: false,
  hydrating: false,

  login: (user, accessToken, refreshToken) => {
    localStorage.setItem(ACCESS_KEY, accessToken)
    localStorage.setItem(REFRESH_KEY, refreshToken)
    writeCachedUser(user)
    set({ user, accessToken, loaded: true, hydrating: false, sessionRevision: get().sessionRevision + 1, marketSwitchError: null, marketSwitchPending: false })
  },

  logout: () => {
    localStorage.removeItem(ACCESS_KEY)
    localStorage.removeItem(REFRESH_KEY)
    writeCachedUser(null)
    try {
      getMarketWebSocket().disconnect()
    } catch {
      // ignore
    }
    // 清空通知状态，避免下一账号看到上一账号的未读红点 / 弹窗
    useNotificationsStore.getState().reset()
    set({ user: null, accessToken: null, loaded: true, hydrating: false, sessionRevision: get().sessionRevision + 1, marketSwitchError: null, marketSwitchPending: false })
  },

  updateUser: (partial) => {
    const prev = get().user
    if (!prev) return
    const next: User = { ...prev, ...partial }
    if (partial.role !== undefined) {
      next.role = partial.role === "admin" ? "admin" : "user"
    }
    writeCachedUser(next)
    set({ user: next })
  },

  switchMarket: async (market) => {
    const { user, accessToken, sessionRevision } = get()
    if (!user || !accessToken) return
    if (queuedSession !== sessionRevision) {
      queuedSession = sessionRevision
      marketQueue = Promise.resolve()
      confirmedMarket = user.market_type ?? "futures"
    }
    const sequence = ++marketSequence
    const optimistic: User = { ...user, market_type: market }
    writeCachedUser(optimistic)
    set({ user: optimistic, marketSwitchError: null, marketSwitchPending: true })
    const pending = marketQueue.then(async () => {
      if (get().sessionRevision !== sessionRevision) return
      try {
        const token = get().accessToken
        if (!token) return
        const result = await switchMarketApi(market, token)
        if (get().sessionRevision !== sessionRevision) return
        confirmedMarket = result.market_type
        const current = get().user
        if (current && sequence === marketSequence) {
          const next: User = { ...current, market_type: confirmedMarket }
          writeCachedUser(next)
          set({ user: next, marketSwitchPending: false })
        }
      } catch (error) {
        if (get().sessionRevision !== sessionRevision || sequence !== marketSequence) return
        const current = get().user
        if (current) {
          const next: User = { ...current, market_type: confirmedMarket }
          writeCachedUser(next)
          set({ user: next, marketSwitchPending: false, marketSwitchError: error instanceof Error ? error.message : "市场切换失败，请重试" })
        }
      }
    })
    marketQueue = pending
    await pending
  },

  setAccessToken: (token) => {
    localStorage.setItem(ACCESS_KEY, token)
    set({ accessToken: token })
  },

  restoreSessionToken: (token) => {
    localStorage.setItem(ACCESS_KEY, token)
    writeCachedUser(null)
    set({ ...beginSessionRestoreState(token), sessionRevision: get().sessionRevision + 1, marketSwitchError: null, marketSwitchPending: false })
  },

  setLoaded: (loaded) => set({ loaded }),
  setHydrating: (hydrating) => set({ hydrating }),
  }
})
