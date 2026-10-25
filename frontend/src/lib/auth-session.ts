const pendingSessions = new Map<string, Promise<unknown>>()

export function beginSessionRestoreState(token: string): { user: null; accessToken: string; loaded: false; hydrating: false } {
  return { user: null, accessToken: token, loaded: false, hydrating: false }
}

export function sessionStillCurrent(token: string, storeToken: string | null, storageToken: string | null): boolean {
  return token === storeToken && token === storageToken
}

export function loadSessionOnce<Result>(token: string, loader: () => Promise<Result>): Promise<Result> {
  const existing = pendingSessions.get(token)
  if (existing) return existing as Promise<Result>
  const pending = loader().finally(() => {
    if (pendingSessions.get(token) === pending) pendingSessions.delete(token)
  })
  pendingSessions.set(token, pending)
  return pending
}
