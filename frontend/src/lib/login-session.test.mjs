import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"

function fixture() {
  const target = new URL("./login-session.ts", import.meta.url)
  const output = ts.transpileModule(existsSync(target) ? readFileSync(target, "utf8") : "export const establishLoginSession = undefined", { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
  const storage = new Map(), calls = []
  let resolveUser, rejectUser
  const pending = new Promise((resolve, reject) => { resolveUser = resolve; rejectUser = reject })
  const state = { user: { id: "old-user" }, accessToken: "old-access", sessionRevision: 1,
    restoreSessionToken(token) { state.user = null; state.accessToken = token; state.sessionRevision++; storage.set("access_token", token) },
    login(user, access, refresh) { state.user = user; state.accessToken = access; state.sessionRevision++; storage.set("access_token", access); storage.set("refresh_token", refresh) },
    logout() { state.user = null; state.accessToken = null; state.sessionRevision++; storage.delete("access_token"); storage.delete("refresh_token") },
  }
  const module = { exports: {} }
  vm.runInNewContext(output, { module, exports: module.exports, localStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) }, require: (name) => {
    if (name === "@/stores/auth") return { useAuthStore: { getState: () => state } }
    if (name === "@/lib/api") return { getBoundSessionApi: (...args) => { calls.push(args); return pending } }
    throw new Error(name)
  } })
  assert.equal(typeof module.exports.establishLoginSession, "function", "login must use the session transition contract")
  return { establish: module.exports.establishLoginSession, state, calls, storage, resolveUser, rejectUser }
}
const tokens = { access_token: "new-access", refresh_token: "new-refresh" }
const bound = { user: { id: "new-user", trading_mode: "virtual" }, accessToken: "new-access", refreshToken: "new-refresh" }

test("login invalidates old identity before loading its bound user", async () => {
  const page = fixture(); const pending = page.establish(tokens, 1)
  assert.equal(page.state.user, null)
  assert.equal(page.state.sessionRevision, 2)
  assert.deepEqual(Array.from(page.calls[0]), ["new-access", "new-refresh"])
  page.resolveUser(bound); assert.equal(await pending, true)
  assert.equal(page.state.user.id, "new-user")
})

test("stale login tokens never replace a newer session", async () => {
  const page = fixture(); page.state.sessionRevision = 2
  assert.equal(await page.establish(tokens, 1), false)
  assert.equal(page.calls.length, 0)
  assert.equal(page.state.accessToken, "old-access")
})

for (const success of [true, false]) test(`bound login ${success ? "success" : "failure"} after logout cannot replace another user`, async () => {
  const page = fixture(); const pending = page.establish(tokens, 1)
  page.state.logout(); page.state.login({ id: "another-user" }, "another-access", "another-refresh")
  if (success) page.resolveUser(bound); else page.rejectUser(new Error("old request failed"))
  if (success) assert.equal(await pending, false); else await assert.rejects(pending)
  assert.equal(page.state.user.id, "another-user")
  assert.equal(page.state.accessToken, "another-access")
})
