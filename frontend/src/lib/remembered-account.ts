const REMEMBER_KEY = "qihuo_login_remember"
const USERNAME_KEY = "qihuo_login_username"
const LEGACY_PASSWORD_KEY = "qihuo_login_password"

export function loadRememberedAccount(): { username: string; remember: boolean } {
  const empty = { username: "", remember: false }
  if (typeof window === "undefined") return empty
  try {
    localStorage.removeItem(LEGACY_PASSWORD_KEY)
    const remember = localStorage.getItem(REMEMBER_KEY) === "1"
    return remember ? { username: localStorage.getItem(USERNAME_KEY) ?? "", remember: true } : empty
  } catch {
    return empty
  }
}

export function saveRememberedAccount(remember: boolean, username: string): void {
  if (typeof window === "undefined") return
  try {
    localStorage.removeItem(LEGACY_PASSWORD_KEY)
    if (remember) {
      localStorage.setItem(REMEMBER_KEY, "1")
      localStorage.setItem(USERNAME_KEY, username)
    } else {
      localStorage.removeItem(REMEMBER_KEY)
      localStorage.removeItem(USERNAME_KEY)
    }
  } catch {
    return
  }
}
