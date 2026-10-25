import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"

const output = ts.transpileModule(readFileSync(new URL("./paper-trading.ts", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
const input = { symbol: "rb2701", symbolName: "fixture", action: "buy", orderType: "limit", price: 3000, quantity: 1, positionDirection: null }
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }

function fixture() {
  let identity = { accessToken: "token-a", sessionRevision: 1, user: { id: "user-a", market_type: "futures", trading_mode: "virtual" } }
  const subscribers = new Set(), calls = []
  const auth = { getState: () => identity, subscribe: (listener) => { subscribers.add(listener); return () => subscribers.delete(listener) } }
  let account = { account_id: "account-a", total_equity: 100 }, estimate = async () => ({}), cancel = async () => ({})
  const api = {
    getPaperAccount: async (...args) => { calls.push(["account", ...args]); return await account },
    getPaperOrders: async (...args) => { calls.push(["orders", ...args]); return { items: [] } },
    getPaperPositions: async (...args) => { calls.push(["positions", ...args]); return { items: [] } },
    getPaperLedgers: async (...args) => { calls.push(["ledgers", ...args]); return { items: [] } },
    estimatePaperOrder: async (...args) => { calls.push(["estimate", ...args]); return await estimate() },
    placePaperOrder: async (...args) => { calls.push(["place", ...args]); return { id: "order-a", status: "pending", symbol: "rb2701", quantity: 1, price: 3000 } },
    cancelPaperOrder: async (...args) => { calls.push(["cancel", ...args]); return await cancel() },
  }
  const module = { exports: {} }
  const dependencies = {
    zustand: { create: (initialize) => { let state; const get = () => state; state = initialize((patch) => { state = { ...state, ...patch } }, get); return { getState: get, setState: (patch) => { state = { ...state, ...patch } } } } },
    "@/stores/auth": { useAuthStore: auth }, "@/lib/paper-api": api,
    "@/lib/api": { getSessionStatusApi: async () => ({ is_open: true }) },
  }
  vm.runInNewContext(output, { module, exports: module.exports, Error, require: (name) => { assert.ok(name in dependencies, name); return dependencies[name] } })
  return { store: module.exports.usePaperTradingStore, calls, setAccount: (value) => { account = value }, setEstimate: (value) => { estimate = value }, setCancel: (value) => { cancel = value }, change: (patch) => { const previous = identity; identity = { ...identity, ...patch }; for (const subscriber of subscribers) subscriber(identity, previous) } }
}

for (const patch of [{ accessToken: "token-b", sessionRevision: 2, user: { id: "user-b", market_type: "futures", trading_mode: "live" } }, { sessionRevision: 2 }, { user: { id: "user-a", market_type: "stock", trading_mode: "virtual" } }, { accessToken: null, user: null }]) test("account cache clears immediately and late response cannot revive a changed scope " + JSON.stringify(patch), async () => {
  const subject = fixture()
  await subject.store.getState().refresh()
  assert.ok(subject.store.getState().account)
  const pending = deferred(); subject.setAccount(pending.promise)
  const refresh = subject.store.getState().refresh()
  subject.change(patch)
  assert.equal(subject.store.getState().account, null)
  pending.resolve({ account_id: "old-private-account" }); await refresh
  assert.equal(subject.store.getState().account, null)
  assert.equal(subject.store.getState().error, null)
})

test("all financial reads use the captured original token and latest refresh wins", async () => {
  const subject = fixture(), first = deferred(), second = deferred()
  subject.setAccount(first.promise); const one = subject.store.getState().refresh()
  subject.setAccount(second.promise); const two = subject.store.getState().refresh()
  second.resolve({ account_id: "latest" }); await two
  first.resolve({ account_id: "earlier" }); await one
  assert.equal(subject.store.getState().account.account_id, "latest")
  for (const call of subject.calls) assert.equal(call.at(-1), "token-a")
})

test("switching login during order estimate never submits the old intent to the new user", async () => {
  const subject = fixture(), gate = deferred()
  subject.setEstimate(() => gate.promise)
  const pending = subject.store.getState().place(input)
  subject.change({ accessToken: "token-b", sessionRevision: 2 })
  gate.resolve({}); assert.equal(await pending, null)
  assert.equal(subject.calls.filter((call) => call[0] === "place").length, 0)
  assert.equal(subject.store.getState().error, null)
})

test("bulk cancellation stops at the original session boundary without using replacement token", async () => {
  const subject = fixture(), gate = deferred()
  await subject.store.getState().refresh()
  subject.store.setState({ orders: [{ id: "one", status: "pending" }, { id: "two", status: "pending" }] })
  subject.setCancel(() => gate.promise)
  const pending = subject.store.getState().cancelAllPending()
  subject.change({ accessToken: "token-b", sessionRevision: 2 })
  gate.resolve({}); await pending
  assert.equal(subject.calls.filter((call) => call[0] === "cancel").length, 1)
  assert.equal(subject.calls.find((call) => call[0] === "cancel").at(-1), "token-a")
  assert.equal(subject.store.getState().lastMessage, null)
})

test("a stock session cannot start a futures financial refresh or order", async () => {
  const subject = fixture()
  subject.change({ user: { id: "user-a", market_type: "stock", trading_mode: "virtual" } })
  await subject.store.getState().refresh()
  assert.equal(await subject.store.getState().place(input), null)
  assert.equal(subject.calls.length, 0)
})

test("pending market confirmation clears finances and invalidates pre-switch reads even after returning to the same market", async () => {
  const subject = fixture(), gate = deferred()
  await subject.store.getState().refresh()
  subject.setAccount(gate.promise); const pending = subject.store.getState().refresh()
  subject.change({ marketSwitchPending: true })
  assert.equal(subject.store.getState().account, null)
  const priorCalls = subject.calls.length
  await subject.store.getState().refresh()
  assert.equal(await subject.store.getState().place(input), null)
  assert.equal(subject.calls.length, priorCalls)
  subject.change({ marketSwitchPending: false })
  gate.resolve({ account_id: "pre-switch-read" }); await pending
  assert.equal(subject.store.getState().account, null)
  subject.setAccount({ account_id: "confirmed-scope" }); await subject.store.getState().refresh()
  assert.equal(subject.store.getState().account.account_id, "confirmed-scope")
})

test("summary polling reads only the account and cannot overwrite a newer account refresh", async () => {
  const subject = fixture(), earlier = deferred(), latest = deferred()
  subject.setAccount(earlier.promise); const first = subject.store.getState().refresh()
  subject.setAccount(latest.promise); const second = subject.store.getState().refreshAccount()
  latest.resolve({ account_id: "latest-account-only" }); await second
  earlier.resolve({ account_id: "earlier-full-refresh" }); await first
  assert.equal(subject.store.getState().account.account_id, "latest-account-only")
  assert.equal(subject.calls.length, 5)
})
