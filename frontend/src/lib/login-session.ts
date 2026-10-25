import { getBoundSessionApi } from "@/lib/api"
import { useAuthStore } from "@/stores/auth"

export async function establishLoginSession(tokens: { access_token: string; refresh_token: string }, expectedRevision: number): Promise<boolean> {
  if (useAuthStore.getState().sessionRevision !== expectedRevision) return false
  useAuthStore.getState().restoreSessionToken(tokens.access_token)
  localStorage.setItem("refresh_token", tokens.refresh_token)
  const revision = useAuthStore.getState().sessionRevision
  const current = () => useAuthStore.getState().sessionRevision === revision && useAuthStore.getState().accessToken === tokens.access_token && localStorage.getItem("access_token") === tokens.access_token
  try {
    const session = await getBoundSessionApi(tokens.access_token, tokens.refresh_token)
    if (!current()) return false
    useAuthStore.getState().login(session.user, session.accessToken, session.refreshToken)
    return true
  } catch (error) {
    if (current()) useAuthStore.getState().logout()
    throw error
  }
}
