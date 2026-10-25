interface AuthSnapshot {
  user: { id: string; market_type?: string } | null
  accessToken: string | null
  sessionRevision: number
  marketSwitchPending?: boolean
  hydrating?: boolean
  loaded?: boolean
}

export interface AiClientScope {
  key: string
  preferenceKey: string
  accessToken: string
  userId: string
  market: "stock" | "futures"
  mode: "live" | "virtual"
}

export function captureAiScope(auth: AuthSnapshot): AiClientScope | null {
  const { user, accessToken, sessionRevision } = auth
  if (!user || !accessToken || !Number.isSafeInteger(sessionRevision) || auth.marketSwitchPending || auth.hydrating || auth.loaded === false) return null
  const market = user.market_type
  if (market !== "stock" && market !== "futures") return null
  try {
    const encoded = accessToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")
    const claims = JSON.parse(atob(encoded)) as { sub?: string; trading_mode?: string }
    if (claims.sub !== user.id || (claims.trading_mode !== "live" && claims.trading_mode !== "virtual")) return null
    const mode = claims.trading_mode
    return {
      key: JSON.stringify([user.id, market, mode, sessionRevision]),
      preferenceKey: `ai:selected-model:${user.id}:${market}:${mode}`,
      accessToken,
      userId: user.id,
      market,
      mode,
    }
  } catch {
    return null
  }
}
