import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import vm from "node:vm"
import ts from "typescript"
import { loadPendingWrites, memoryStorage } from "../../lib/stock-pending-writes-harness.mjs"

const elements = (tree) => Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : []
const text = (tree) => Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree)
const accountId = "10000000-0000-4000-8000-000000000001"

function mount() {
  const slots = [], effects = [], calls = []
  let cursor = 0, props = { accountId, revision: 210, currentDate: "2026-09-03" }, auth = { accessToken: "captured-token", user: { id: "owner", market_type: "stock", trading_mode: "virtual" } }
  const dependencies = {
    react: {
      useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial; return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value }] },
      useRef(initial) { const index = cursor++; return slots[index] ??= { current: initial } },
      useEffect(callback, deps) { const index = cursor++; if (!(index in slots) || deps.some((value, position) => value !== slots[index].deps[position])) { slots[index]?.cleanup?.(); slots[index] = { deps }; effects.push(() => { slots[index].cleanup = callback() }) } },
    },
    "@/stores/auth": { useAuthStore: (selector) => selector(auth) },
    "@/lib/stock-replay-history-api": { getStockReplayOrderHistory: (account, query, token, mode, signal) => new Promise((resolve, reject) => calls.push({ account, query, token, mode, signal, resolve, reject })) },
  }
  const source = readFileSync(new URL("./stock-replay-order-history.tsx", import.meta.url), "utf8")
  const module = { exports: {} }, requireModule = createRequire(import.meta.url)
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText, { module, exports: module.exports, AbortController, Error, require: (name) => dependencies[name] ?? requireModule(name) })
  const render = () => { cursor = 0; const tree = module.exports.StockReplayOrderHistory(props); effects.splice(0).forEach((effect) => effect()); return tree }
  const button = (label) => elements(render()).find((item) => item.type === "button" && text(item) === label)
  render()
  return { render, calls, button, props(patch) { props = { ...props, ...patch }; render() }, auth(patch) { auth = { ...auth, ...patch }; render() }, filter(label, value) { elements(render()).find((item) => item.props?.["aria-label"] === label).props.onChange({ target: { value } }); render() }, settle: () => new Promise(setImmediate), unmount() { slots.forEach((slot) => slot?.cleanup?.()) } }
}

function payload(query) {
  return { query, account_id: accountId, revision: 210, current_date: "2026-09-03", total: 21, items: [{ id: "order", code: "000001", direction: "buy", quantity: 100, order_type: "market", status: "filled", reason: null, submitted_on: "2026-09-01", filled_on: "2026-09-02", filled_price: "10.0000", limit_price: null, fees: "5.01", cash_delta: "-1005.01", realized_pnl: "0.00" }], has_more: query.offset === 0, limitations: ["不是券商账单"], history_version: "stock-replay-order-history-v1" }
}

test("all stored order history is explicit with true server pagination and exact amounts", async () => {
  const page = mount(); assert.equal(page.calls.length, 0)
  page.button("查询全部委托历史").props.onClick(); assert.equal(page.calls[0].token, "captured-token"); assert.equal(page.calls[0].query.expected_revision, 210)
  page.calls[0].resolve(payload(page.calls[0].query)); await page.settle()
  assert.match(text(page.render()), /5\.01/); assert.match(text(page.render()), /-1005\.01/); assert.match(text(page.render()), /21/)
  page.button("历史下一页").props.onClick(); assert.equal(page.calls[1].query.offset, 20)
  page.calls[1].resolve(payload(page.calls[1].query)); await page.settle()
  assert.equal(page.button("历史下一页").props.disabled, true)
  page.unmount(); assert.equal(page.calls[1].signal.aborted, true)
})

test("account revision mode auth and filter changes isolate results including late responses", async () => {
  const page = mount(); page.button("查询全部委托历史").props.onClick()
  page.props({ revision: 211 }); assert.equal(page.calls[0].signal.aborted, true)
  page.calls[0].resolve(payload(page.calls[0].query)); await page.settle(); assert.doesNotMatch(text(page.render()), /-1005\.01/)
  page.button("查询全部委托历史").props.onClick(); page.auth({ accessToken: "new-token", user: { id: "other", market_type: "stock", trading_mode: "live" } }); assert.equal(page.calls[1].signal.aborted, true)
  page.button("查询全部委托历史").props.onClick(); assert.equal(page.calls[2].mode, "live")
  page.filter("历史委托状态", "rejected"); assert.equal(page.calls[2].signal.aborted, true)
  page.props({ accountId: "10000000-0000-4000-8000-000000000002" })
  page.auth({ user: { id: "other", market_type: "futures", trading_mode: "virtual" } })
  assert.equal(page.button("查询全部委托历史").props.disabled, true)
})

test("revision errors cannot be retried as new pages and transient failures never become empty success", async () => {
  const page = mount(); page.button("查询全部委托历史").props.onClick()
  page.calls[0].reject(Object.assign(new Error("请刷新账户"), { requires_refresh: true })); await page.settle()
  assert.match(text(page.render()), /刷新账户/)
  assert.equal(page.button("重试历史查询").props.disabled, true)
  page.props({ revision: 211 }); page.button("查询全部委托历史").props.onClick()
  page.calls[1].reject(new Error("HTTP 503")); await page.settle()
  assert.match(text(page.render()), /503/); assert.doesNotMatch(text(page.render()), /没有匹配的委托/)
  page.button("重试历史查询").props.onClick(); assert.equal(page.calls.length, 3)
})

test("completed replay workspace mounts history independently of trading and preserves old orders", () => {
  const History = () => null, wrapper = (props) => props.children, icon = () => null
  const view = { id: accountId, name: "已结束账户", status: "completed", revision: 210, current_date: "2026-09-03", start_date: "2026-09-01", end_date: "2026-09-03", cash: "10000.00", equity: "10000.00", available_cash: "10000.00", reserved_cash: "0.00", total_pnl: "0.00", realized_pnl: "0.00", fees_paid: "0.00", orders: [], positions: [], instruments: [{ code: "000001", name: "证券" }], chart: { code: "000001", bars: [] }, data_versions: [] }
  let stateIndex = 0
  const overrides = { 0: { enabled: true, datasets: [] }, 3: accountId, 5: view, 6: false }
  const dependencies = {
    react: { useState(initial) { const index = stateIndex++; return [index in overrides ? overrides[index] : typeof initial === "function" ? initial() : initial, () => {}] }, useRef: (current) => ({ current }), useCallback: (callback) => callback, useEffect() {} },
    "lucide-react": { ArrowRight: icon, ChevronLeft: icon, ChevronRight: icon, Loader2: icon, Plus: icon, RefreshCw: icon },
    "@/components/market/stock-kline-chart": { StockKlineChart: icon }, "@/components/ui/button": { Button: wrapper }, "@/components/ui/input": { Input: wrapper },
    "@/stores/auth": { useAuthStore: { getState: () => ({ user: { id: "owner", market_type: "stock", trading_mode: "virtual" } }) } },
    "@/stores/display": { useDisplayStore: (selector) => selector({ candleUp: "red", candleDown: "green" }) }, "@/lib/utils": { cn: (...values) => values.filter(Boolean).join(" ") },
    "@/lib/stock-replay-api": {}, "./stock-replay-state": { replayAccountStorageKey: () => "unused" }, "./stock-replay-order-history": { StockReplayOrderHistory: History },
    "@/components/factor-research/stock-pending-writes-banner": { StockPendingWritesBanner: () => null },
    "@/lib/stock-pending-writes": loadPendingWrites(memoryStorage()),
  }
  const module = { exports: {} }, requireModule = createRequire(import.meta.url)
  const source = readFileSync(new URL("./stock-replay-workspace.tsx", import.meta.url), "utf8")
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText + "\nmodule.exports.TestSession = ReplaySession;", { module, exports: module.exports, require: (name) => dependencies[name] ?? requireModule(name) })
  const tree = module.exports.TestSession({ userId: "owner", tradingMode: "virtual", initialTab: "orders" })
  const history = elements(tree).find((item) => item.type === History)
  assert.ok(history); assert.equal(history.props.accountId, accountId); assert.equal(history.props.revision, 210); assert.equal(history.props.disabled, false)
  assert.ok(elements(tree).some((item) => typeof item.type === "function" && item.type.name === "ReplayOrders"))
})
