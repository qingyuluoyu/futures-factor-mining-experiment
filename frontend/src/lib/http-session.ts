import { useAuthStore } from "@/stores/auth"

export interface HttpSession {
  revision: number
  userId: string | null
  accessToken: string | null
  refreshToken: string | null
}

let rotation: { source: HttpSession; target: HttpSession } | null = null
let inFlight: { source: HttpSession; promise: Promise<boolean> } | null = null

export function captureHttpSession(): HttpSession {
  const state = useAuthStore.getState()
  return {
    revision: state.sessionRevision,
    userId: state.user?.id ?? null,
    accessToken: typeof window === "undefined" ? null : localStorage.getItem("access_token"),
    refreshToken: typeof window === "undefined" ? null : localStorage.getItem("refresh_token"),
  }
}

function sameIdentity(first: HttpSession, second: HttpSession): boolean {
  return first.revision === second.revision && first.userId === second.userId
}

function sameCredentials(first: HttpSession, second: HttpSession): boolean {
  return first.accessToken === second.accessToken && first.refreshToken === second.refreshToken
}

export function isHttpSessionCurrent(source: HttpSession): boolean {
  const current = captureHttpSession()
  return sameIdentity(source, current) && (sameCredentials(source, current) || Boolean(
    rotation && sameIdentity(source, rotation.source) && sameCredentials(source, rotation.source) && sameCredentials(current, rotation.target),
  ))
}

export function assertHttpSessionCurrent(source: HttpSession): void {
  if (!isHttpSessionCurrent(source)) throw new Error("会话已变更，已丢弃旧请求结果")
}

export function expireHttpSession(source: HttpSession): void {
  if (!isHttpSessionCurrent(source)) return
  useAuthStore.getState().logout()
  if (typeof window !== "undefined") window.location.href = "/login"
}

export async function refreshHttpSession(source: HttpSession, apiBase: string): Promise<boolean> {
  if (!isHttpSessionCurrent(source)) return false
  const current = captureHttpSession()
  if (!sameCredentials(source, current)) return Boolean(current.accessToken)
  if (!source.refreshToken) return false
  if (inFlight && sameIdentity(source, inFlight.source) && sameCredentials(source, inFlight.source)) return inFlight.promise

  const promise = (async () => {
    try {
      const response = await fetch(`${apiBase}/api/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: source.refreshToken }),
        signal: AbortSignal.timeout(20000),
      })
      if (!response.ok || !isHttpSessionCurrent(source)) return false
      const data = await response.json()
      if (!isHttpSessionCurrent(source) || typeof data?.access_token !== "string" || !data.access_token.trim()
        || (data.refresh_token !== undefined && (typeof data.refresh_token !== "string" || !data.refresh_token.trim()))) return false
      const target = { ...source, accessToken: data.access_token, refreshToken: data.refresh_token ?? source.refreshToken }
      useAuthStore.getState().setAccessToken(target.accessToken)
      if (target.refreshToken) localStorage.setItem("refresh_token", target.refreshToken)
      rotation = { source, target }
      return true
    } catch {
      return false
    }
  })()
  inFlight = { source, promise }
  try {
    return await promise
  } finally {
    if (inFlight?.promise === promise) inFlight = null
  }
}
