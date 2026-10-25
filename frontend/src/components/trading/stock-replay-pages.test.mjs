import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { randomUUID } from "node:crypto"
import vm from "node:vm"
import ts from "typescript"
import * as replayState from "./stock-replay-state.ts"
import { loadPendingWrites } from "../../lib/stock-pending-writes-harness.mjs"

const elements = (tree) => Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : []
const text = (tree) => Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree)
const plain = (value) => JSON.parse(JSON.stringify(value))
const view = { id: "account-a", trading_mode: "virtual", name: "本人回放", status: "active", revision: 7, current_date: "2026-10-16", start_date: "2026-10-15", end_date: "2026-10-23", initial_cash: "123456.78", cash: "10000.00", equity: "11000.00", available_cash: "10000.00", reserved_cash: "0.00", total_pnl: "0.00", realized_pnl: "0.00", fees_paid: "5.00", orders: [{ id: "order-a", status: "pending" }], positions: [{ code: "000001", quantity: 100, available_quantity: 0 }], instruments: [{ code: "000001", name: "证券" }], chart: { code: "000001", bars: [{ time: "2026-10-16", open: 10, high: 11, low: 9, close: 10, volume: 100 }] }, data_versions: [], currency: "CNY", method_version: "existing-method" }

function mount(role = "overview", api = {}, store = new Map()) {
  const states = [], calls = [], storage = store
  let cursor = 0, auth = { id: "owner", market_type: "stock", trading_mode: "virtual" }
  const Chart = () => null, History = () => null, Button = () => null, Input = () => null, Link = () => null, Banner = () => null
  const overrides = { 0: { enabled: true, datasets: [{ id: "data-a", code: "000001", name: "证券", start_date: "2026-10-15", end_date: "2026-10-23", source: "authorized-fixture", version: "v1" }] }, 1: [{ id: "account-a", name: "本人回放", current_date: "2026-10-16" }, { id: "account-b", name: "另一个回放", current_date: "2026-10-15" }], 3: view.id, 5: view, 6: false }
  const localStorage = { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) }
  const dependencies = {
    react: { useState(initial) { const i = cursor++; if (!(i in states)) states[i] = i in overrides ? overrides[i] : typeof initial === "function" ? initial() : initial; return [states[i], (next) => { states[i] = typeof next === "function" ? next(states[i]) : next }] }, useRef(value) { const i = cursor++; return states[i] ??= { current: value } }, useCallback: (callback) => callback, useEffect() {} },
    "next/link": { default: Link },
    "@/components/market/stock-kline-chart": { StockKlineChart: Chart },
    "./stock-replay-order-history": { StockReplayOrderHistory: History },
    "@/components/ui/button": { Button }, "@/components/ui/input": { Input },
    "@/stores/auth": { useAuthStore: Object.assign((selector) => selector({ loaded: true, user: auth }), { getState: () => ({ user: auth }) }) },
    "@/stores/display": { useDisplayStore: (selector) => selector({ candleUp: "red", candleDown: "green" }) },
    "@/lib/utils": { cn: (...items) => items.filter(Boolean).join(" ") },
    "./stock-replay-state": replayState,
    "@/components/factor-research/stock-pending-writes-banner": { StockPendingWritesBanner: Banner },
    "@/lib/stock-pending-writes": loadPendingWrites(localStorage, { crypto }),
    "@/lib/stock-replay-api": Object.fromEntries(["createStockReplayAccount", "submitStockReplayOrder", "advanceStockReplayAccount", "cancelStockReplayOrder"].map((name) => [name, async (...args) => { calls.push({ name, args }); return api[name] ? api[name](...args) : { ...view, revision: 8 } }])),
  }
  const module = { exports: {} }, requireModule = createRequire(import.meta.url)
  const source = readFileSync(new URL("./stock-replay-workspace.tsx", import.meta.url), "utf8")
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText + "\nmodule.exports.TestSession = ReplaySession;", { module, exports: module.exports, crypto: { randomUUID }, localStorage, require: (name) => dependencies[name] ?? requireModule(name) })
  const render = () => { cursor = 0; return module.exports.TestSession({ userId: "owner", tradingMode: "virtual", initialTab: role }) }
  return { render, calls, Chart, History, Button, Input, Link, Banner, storage, pendingWrites: dependencies["@/lib/stock-pending-writes"], role(value) { role = value }, auth(value) { auth = value }, settle: () => new Promise(setImmediate) }
}

test("ledger pages render their own primary records without charts order form or date advance", () => {
  for (const [role, component, heading] of [["positions", "ReplayPositions", "A股回放持仓"], ["orders", "ReplayOrders", "A股回放订单"], ["assets", "ReplayAssets", "A股回放资产"]]) {
    const page = mount(role), tree = page.render(), items = elements(tree)
    assert.match(text(tree), new RegExp(heading))
    assert.ok(items.some((item) => item.type?.name === component))
    assert.equal(items.some((item) => item.type === page.Chart), false)
    assert.equal(items.some((item) => item.props?.["aria-label"] === "回放下单与日期推进"), false)
    assert.equal(items.some((item) => item.type === "form"), false)
    assert.ok(items.some((item) => item.props?.["aria-label"] === "选择股票回放账户"))
    assert.equal(items.some((item) => item.type === page.History), role === "orders")
  }
})

test("trading alone keeps the existing replay chart order form next day controls and recent orders", () => {
  const page = mount(), tree = page.render(), items = elements(tree)
  assert.match(text(tree), /A股日线回放/)
  const chart = items.find((item) => item.type === page.Chart)
  assert.ok(chart)
  assert.equal(chart.props.volumeUnit, "股")
  assert.equal(chart.props.changeBasis, "unavailable")
  assert.equal(chart.props.priceAdjustmentLabel, "不复权（撮合价）")
  assert.ok(items.some((item) => item.props?.["aria-label"] === "回放下单与日期推进"))
  assert.ok(items.some((item) => item.type === "form"))
  assert.ok(items.some((item) => item.type?.name === "ReplayOrders"))
})

test("page role changes immediately without retaining a prior local tab and navigation uses route links", () => {
  const page = mount("positions")
  page.render(); page.role("assets")
  const tree = page.render(), items = elements(tree)
  assert.ok(items.some((item) => item.type?.name === "ReplayAssets"))
  assert.equal(items.some((item) => item.type?.name === "ReplayPositions"), false)
  const links = items.filter((item) => item.type === page.Link)
  for (const href of ["/trading", "/positions", "/orders", "/assets"]) assert.ok(links.some((item) => item.props.href === href))
  assert.equal(links.find((item) => item.props.href === "/assets").props["aria-current"], "page")
  assert.equal(items.some((item) => item.props?.role === "tab"), false)
})

test("ledger account selection clears the previous account view before another account is loaded", () => {
  const page = mount("positions")
  elements(page.render()).find((item) => item.props?.["aria-label"] === "选择股票回放账户").props.onChange({ target: { value: "account-b" } })
  const items = elements(page.render())
  assert.equal(items.find((item) => item.props?.["aria-label"] === "选择股票回放账户").props.value, "account-b")
  assert.equal(items.some((item) => item.type?.name === "ReplayPositions"), false)
})

test("orders keep uncertain cancellation and explicit retry on the original account revision and key", async () => {
  let finish, attempt = 0
  const page = mount("orders", { cancelStockReplayOrder: () => ++attempt === 1 ? new Promise((_resolve, reject) => { finish = reject }) : { ...view, revision: 8 } })
  elements(page.render()).find((item) => item.type?.name === "ReplayOrders").props.onCancel("order-a")
  let tree = page.render()
  assert.equal(elements(tree).find((item) => item.props?.["aria-label"] === "选择股票回放账户").props.disabled, true)
  assert.equal(elements(tree).some((item) => item.type === page.Link), false)
  finish(new Error("network uncertain")); await page.settle()
  tree = page.render()
  assert.match(text(tree), /重试同一请求/)
  assert.equal(elements(tree).some((item) => item.type === page.Link), false)
  elements(tree).find((item) => item.type === page.Button && text(item) === "重试同一请求").props.onClick()
  assert.equal(page.calls.length, 2)
  assert.equal(page.calls[0].name, "cancelStockReplayOrder")
  assert.equal(page.calls[1].args[0], "account-a")
  assert.equal(page.calls[1].args[1], "order-a")
  assert.equal(page.calls[1].args[2].expected_revision, 7)
  assert.equal(page.calls[1].args[2].idempotency_key, page.calls[0].args[2].idempotency_key)
  await page.settle()
  tree = page.render()
  assert.equal(elements(tree).filter((item) => item.type === page.Link).length, 4)
  assert.equal(elements(tree).find((item) => item.props?.["aria-label"] === "选择股票回放账户").props.disabled, false)
  assert.equal(elements(tree).find((item) => item.type === page.History).props.revision, 8)
})

test("ledger cancellation never writes after the active identity or market context changes", () => {
  for (const auth of [{ id: "other", market_type: "stock", trading_mode: "virtual" }, { id: "owner", market_type: "stock", trading_mode: "live" }, { id: "owner", market_type: "futures", trading_mode: "virtual" }]) {
    const page = mount("orders"), orders = elements(page.render()).find((item) => item.type?.name === "ReplayOrders")
    page.auth(auth); orders.props.onCancel("order-a")
    assert.equal(page.calls.length, 0)
  }
})

test("an unconfirmed replay write survives leaving the page and retries with the original idempotency key", async () => {
  const store = new Map()
  let finish, attempt = 0
  const page = mount("orders", { cancelStockReplayOrder: () => ++attempt === 1 ? new Promise((_resolve, reject) => { finish = reject }) : { ...view, revision: 8 } }, store)
  elements(page.render()).find((item) => item.type?.name === "ReplayOrders").props.onCancel("order-a")
  const original = page.calls[0].args[2].idempotency_key
  const recorded = plain(page.pendingWrites.readPendingWrites("owner:virtual"))
  assert.equal(recorded.length, 1)
  assert.equal(recorded[0].kind, "replay-cancel")
  assert.equal(recorded[0].idempotency_key, original)
  assert.equal(recorded[0].expected_revision, 7)
  finish(new Error("network uncertain")); await page.settle()
  assert.match(text(page.render()), /重试同一请求/)
  assert.equal(plain(page.pendingWrites.readPendingWrites("owner:virtual")).length, 1, "导航离开不得清除未确认写入")
  const returned = mount("orders", { cancelStockReplayOrder: () => ({ ...view, revision: 8 }) }, store)
  const tree = returned.render()
  assert.match(text(tree), /重试同一请求/)
  assert.equal(elements(tree).some((item) => item.type === returned.Link), false)
  elements(tree).find((item) => item.type === returned.Button && text(item) === "重试同一请求").props.onClick()
  await returned.settle()
  assert.equal(returned.calls.length, 1)
  assert.equal(returned.calls[0].name, "cancelStockReplayOrder")
  assert.equal(returned.calls[0].args[0], "account-a")
  assert.equal(returned.calls[0].args[1], "order-a")
  assert.equal(returned.calls[0].args[2].idempotency_key, original)
  assert.equal(returned.calls[0].args[2].expected_revision, 7)
  assert.deepEqual(plain(returned.pendingWrites.readPendingWrites("owner:virtual")), [])
  const settled = returned.render()
  assert.equal(elements(settled).filter((item) => item.type === returned.Link).length, 4)
})

test("a confirmed replay write and a rejected 4xx replay write both clear the pending registry slot", async () => {
  const store = new Map()
  const confirmed = mount("orders", {}, store)
  elements(confirmed.render()).find((item) => item.type?.name === "ReplayOrders").props.onCancel("order-a")
  await confirmed.settle()
  assert.deepEqual(plain(confirmed.pendingWrites.readPendingWrites("owner:virtual")), [])
  const rejected = mount("orders", { cancelStockReplayOrder: () => { throw Object.assign(new Error("委托状态已变化"), { status: 409 }) } }, store)
  elements(rejected.render()).find((item) => item.type?.name === "ReplayOrders").props.onCancel("order-a")
  await rejected.settle()
  assert.deepEqual(plain(rejected.pendingWrites.readPendingWrites("owner:virtual")), [])
  assert.doesNotMatch(text(rejected.render()), /重试同一请求/)
})

test("a rehydrated unconfirmed write never resends itself without an explicit user action", async () => {
  const store = new Map()
  let finish, attempt = 0
  const page = mount("orders", { cancelStockReplayOrder: () => ++attempt === 1 ? new Promise((_resolve, reject) => { finish = reject }) : { ...view, revision: 8 } }, store)
  elements(page.render()).find((item) => item.type?.name === "ReplayOrders").props.onCancel("order-a")
  finish(new Error("network uncertain")); await page.settle()
  const returned = mount("orders", { cancelStockReplayOrder: () => ({ ...view, revision: 8 }) }, store)
  for (let index = 0; index < 3; index += 1) { returned.render(); await returned.settle() }
  assert.equal(returned.calls.length, 0, "回填后不得自动重发写入")
  assert.equal(plain(returned.pendingWrites.readPendingWrites("owner:virtual")).length, 1)
})

test("shared account creation keeps the entered initial cash and existing fee and version contracts", async () => {
  const page = mount("assets")
  elements(page.render()).find((item) => item.type === page.Button && text(item).includes("新建账户")).props.onClick()
  const input = (label) => elements(page.render()).find((item) => item.props?.["aria-label"] === label)
  input("初始模拟资金").props.onChange({ target: { value: "123456.78" } })
  input("回放起始交易日").props.onChange({ target: { value: "2026-10-16" } })
  elements(page.render()).find((item) => item.type === "input" && item.props.type === "checkbox").props.onChange({ target: { checked: true } })
  elements(page.render()).find((item) => item.type === "form").props.onSubmit({ preventDefault() {} })
  await page.settle()
  const body = page.calls[0].args[0]
  assert.equal(body.initial_cash, "123456.78")
  assert.equal(body.commission_rate, "0.0003")
  assert.equal(body.min_commission, "5")
  assert.equal(body.slippage_rate, "0.001")
  assert.ok(body.idempotency_key)
  assert.equal(page.storage.get("stock-replay-account:owner:virtual"), "account-a")
})

test("the replay pages announce unconfirmed writes this page does not retry itself", () => {
  for (const role of ["overview", "positions", "orders", "assets"]) {
    const page = mount(role, {}, new Map([["stock-pending-writes:owner:virtual", JSON.stringify([
      { kind: "replay-order", idempotency_key: "k1", signature: "[]", created_at: "2026-10-26T00:00:00.000Z" },
      { kind: "dataset-job", idempotency_key: "k2", signature: "[]", created_at: "2026-10-26T00:00:00.000Z" }])]]))
    const banner = elements(page.render()).find((item) => item.type === page.Banner)
    assert.ok(banner, role)
    assert.equal(banner.props.scope, "owner:virtual")
    // 本页已在行内用原幂等键重试 replay-*，横幅不得重复提示
    assert.equal(banner.props.ignore("replay-order"), true)
    assert.equal(banner.props.ignore("replay-cancel"), true)
    assert.equal(banner.props.ignore("dataset-job"), false)
    assert.equal(banner.props.ignore("report-bookmark"), false)
  }
})
