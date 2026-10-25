import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import vm from "node:vm"
import ts from "typescript"

function fixture(blocked = false, withWindow = true) {
  const values = new Map([["qihuo_login_remember", "1"], ["qihuo_login_username", "fixture-user"], ["qihuo_login_password", "fixture-not-real-secret"]])
  const reads = [], writes = [], removed = []
  const storage = { getItem(key) { reads.push(key); if (blocked) throw new Error("storage disabled"); return values.get(key) ?? null }, setItem(key, value) { writes.push(key); if (blocked) throw new Error("storage disabled"); values.set(key, value) }, removeItem(key) { removed.push(key); if (blocked) throw new Error("storage disabled"); values.delete(key) } }
  const source = readFileSync(new URL("./remembered-account.ts", import.meta.url), "utf8")
  const module = { exports: {} }
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, { module, exports: module.exports, ...(withWindow ? { window: {} } : {}), localStorage: storage })
  return { ...module.exports, values, reads, writes, removed }
}

test("legacy plaintext password is deleted without ever reading its value", () => {
  const state = fixture()
  const saved = state.loadRememberedAccount()
  assert.equal(saved.username, "fixture-user")
  assert.equal(saved.remember, true)
  assert.equal("password" in saved, false)
  assert.equal(state.reads.includes("qihuo_login_password"), false)
  assert.equal(state.values.has("qihuo_login_password"), false)
})

test("username opt in and out never writes password or token", () => {
  const state = fixture()
  state.saveRememberedAccount(true, "new-user")
  assert.equal(state.loadRememberedAccount().username, "new-user")
  assert.equal(state.writes.some((key) => /password|token/.test(key)), false)
  state.saveRememberedAccount(false, "")
  assert.equal(state.loadRememberedAccount().remember, false)
  assert.equal(state.values.has("qihuo_login_username"), false)
})

test("unavailable browser storage does not prevent ordinary login", () => {
  const state = fixture(true)
  assert.equal(state.loadRememberedAccount().remember, false)
  assert.doesNotThrow(() => state.saveRememberedAccount(true, "fixture"))
})

test("server rendering never accesses browser preferences", () => {
  const state = fixture(false, false)
  assert.equal(state.loadRememberedAccount().remember, false)
  state.saveRememberedAccount(true, "fixture")
  assert.equal(state.reads.length + state.writes.length + state.removed.length, 0)
})

test("both actual login forms use username-only preference helpers", () => {
  for (const path of ["../app/(auth)/login/page.tsx", "../components/landing/auth/auth-login-form.tsx"]) {
    const source = readFileSync(new URL(path, import.meta.url), "utf8")
    assert.match(source, /loadRememberedAccount/)
    assert.match(source, /saveRememberedAccount/)
    assert.match(source, /记住用户名/)
    assert.doesNotMatch(source, /qihuo_login_password|SAVED_PASS_KEY|saved\.password|记住密码/)
  }
})
