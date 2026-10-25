type AlertPresentationSession = {
  user: { market_type?: string } | null
  accessToken: string | null
  loaded: boolean
  hydrating?: boolean
  marketSwitchPending?: boolean
}

export function canPresentFuturesAlerts(session: AlertPresentationSession): boolean {
  return session.loaded === true && !session.hydrating && session.user?.market_type === "futures" && Boolean(session.accessToken) && !session.marketSwitchPending
}
