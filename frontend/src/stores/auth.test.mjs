import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"

const output = ts.transpileModule(readFileSync(new URL("./auth.ts", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
const userA = { id: "synthetic-a", username: "synthetic-a", email: "first@example.test", market_type: "futures", trading_mode: "virtual" }
const userB = { ...userA, id: "synthetic-b", username: "synthetic-b" }
const settle = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve() }

function fixture() {
  const storage = new Map(), calls = []
  const dependencies = {
    zustand: { create: (initialize) => { let state; const get = () => state; state = initialize((patch) => { state = { ...state, ...patch } }, get); return { getState: get } } },
    "@/lib/api": { switchMarketApi: (...args) => new Promise((resolve, reject) => calls.push({ args, resolve, reject })) },
    "@/lib/websocket": { getMarketWebSocket: () => ({ disconnect() {} }) },
    "@/stores/notifications": { useNotificationsStore: { getState: () => ({ reset() {} }) } },
    "@/lib/auth-session": { beginSessionRestoreState: (token) => ({ user: null, accessToken: token, loaded: false, hydrating: false }) },
  }
  const module = { exports: {} }
  vm.runInNewContext(output, { module, exports: module.exports, window: {}, localStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) }, require: (name) => { assert.ok(name in dependencies, name); return dependencies[name] } })
  return { store: module.exports.useAuthStore, calls, storage }
}

test("failed market change after logout never revives an old identity", async () => {
  const { store, calls } = fixture()
  store.getState().login(userA, "access-a", "refresh-a")
  const pending = store.getState().switchMarket("stock"); await settle()
  store.getState().logout(); calls[0].reject(new Error("test failure")); await pending
  assert.equal(store.getState().user, null)
  assert.equal(store.getState().accessToken, null)
})

for (const success of [false, true]) test(`old market ${success ? "success" : "failure"} cannot affect a different login`, async () => {
  const { store, calls } = fixture()
  store.getState().login(userA, "access-a", "refresh-a")
  const pending = store.getState().switchMarket("stock"); await settle()
  store.getState().logout(); store.getState().login(userB, "access-b", "refresh-b")
  if (success) calls[0].resolve({ market_type: "stock" }); else calls[0].reject(new Error("test failure"))
  await pending
  assert.equal(store.getState().user.id, userB.id)
  assert.equal(store.getState().user.market_type, "futures")
  assert.equal(store.getState().accessToken, "access-b")
})

test("same user logging in again invalidates pending work even with the same token", async () => {
  const { store, calls } = fixture()
  store.getState().login(userA, "access-a", "refresh-a")
  const pending = store.getState().switchMarket("stock"); await settle()
  store.getState().login(userA, "access-a", "refresh-a")
  calls[0].resolve({ market_type: "stock" }); await pending
  assert.equal(store.getState().user.market_type, "futures")
})

test("market updates are serialized and persist the latest intention", async () => {
  const { store, calls } = fixture()
  store.getState().login(userA, "access-a", "refresh-a")
  const first = store.getState().switchMarket("stock"); await settle()
  const second = store.getState().switchMarket("futures"); await settle()
  assert.equal(store.getState().marketSwitchPending, true)
  assert.equal(calls.length, 1)
  assert.equal(store.getState().user.market_type, "futures")
  assert.deepEqual(Array.from(calls[0].args), ["stock", "access-a"])
  calls[0].resolve({ market_type: "stock" }); await first; await settle()
  assert.equal(store.getState().marketSwitchPending, true)
  assert.equal(store.getState().user.market_type, "futures")
  assert.equal(calls.length, 2)
  calls[1].resolve({ market_type: "futures" }); await second
  assert.equal(store.getState().marketSwitchPending, false)
  assert.equal(store.getState().user.market_type, "futures")
})

test("latest failure rolls back to confirmed market without reverting newer personal data", async () => {
  const { store, calls } = fixture()
  store.getState().login(userA, "access-a", "refresh-a")
  const first = store.getState().switchMarket("stock"); await settle()
  const second = store.getState().switchMarket("futures")
  calls[0].resolve({ market_type: "stock" }); await first; await settle()
  store.getState().updateUser({ email: "changed@example.test" })
  calls[1].reject(new Error("preference unavailable")); await second
  assert.equal(store.getState().marketSwitchPending, false)
  assert.equal(store.getState().user.market_type, "stock")
  assert.equal(store.getState().user.email, "changed@example.test")
  assert.ok(store.getState().marketSwitchError)
})

test("session restore discards queued calls before they are sent", async () => {
  const { store, calls } = fixture()
  store.getState().login(userA, "access-a", "refresh-a")
  const first = store.getState().switchMarket("stock"); await settle()
  const second = store.getState().switchMarket("futures")
  store.getState().restoreSessionToken("restored-token")
  assert.equal(store.getState().marketSwitchPending, false)
  calls[0].reject(new Error("old session")); calls[1]?.reject(new Error("unexpected old request")); await Promise.all([first, second])
  assert.equal(calls.length, 1)
  assert.equal(store.getState().user, null)
  assert.equal(store.getState().accessToken, "restored-token")
})

test("signed out market changes do not make an authenticated request", async () => {
  const { store, calls } = fixture()
  const pending = store.getState().switchMarket("stock")
  await settle()
  assert.equal(calls.length, 0)
  await pending
})

test("queued market change uses a refreshed token only within the same session", async () => {
  const { store, calls } = fixture()
  store.getState().login(userA, "access-a", "refresh-a")
  const first = store.getState().switchMarket("stock"); await settle()
  const second = store.getState().switchMarket("futures")
  store.getState().setAccessToken("refreshed-a")
  calls[0].resolve({ market_type: "stock" }); await first; await settle()
  assert.deepEqual(Array.from(calls[1].args), ["futures", "refreshed-a"])
  calls[1].resolve({ market_type: "futures" }); await second
  assert.equal(store.getState().user.market_type, "futures")
})
