import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import vm from "node:vm"
import test from "node:test"
import ts from "typescript"
import { captureAiScope } from "../../lib/ai-client-scope.ts"
import { parseAvailabilityPeriod } from "../../lib/stock-data-availability-api.ts"

const requireModule = createRequire(import.meta.url)
const GapBrowser = () => null
const elements = (tree) => Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : []
const text = (tree) => Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? typeof tree.type === "function" ? text(tree.type(tree.props)) : text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree)
const token = (id) => `header.${Buffer.from(JSON.stringify({ sub: id, trading_mode: "virtual" })).toString("base64url")}.signature`
const ready = (data) => ({ state: "ready", data })
const payload = {
  market: ready({ as_of: "2026-10-19", source: "fixture-provider", price_adjustment: "none", coverage: { instruments: 3, quoted: 2, comparable: 1 }, missing_codes: ["600003"], unknown_codes: ["600009"] }),
  coverage: ready({ available: { securities: 2, price_rows: 100, first_trade_date: "2026-07-01", last_trade_date: "2026-10-19", observed_dates: 60 }, requested: { start_date: "2026-07-01", end_date: "2026-10-19" }, selected: { securities: 2, observed_dates: 60, price_rows: 100, missing_price_cells: 20 }, status: "within_limits", as_of: "2026-10-20T10:00:00Z", warnings: ["fixture-历史时点未认证"] }),
  replay: ready({ enabled: true, datasets: Array.from({ length: 100 }, (_, index) => ({ id: `id-${index}`, code: "600001", instrument_id: "600001.SH", name: "fixture-replay", version: `version-${index}`, start_date: "2026-07-01", end_date: "2026-10-19", source: "fixture-provider", adjustment: "none" })) }),
  research: ready({ enabled: true, accepting: false, reason: "fixture-worker离线" }),
}

function mount() {
  let auth = { user: { id: "a", market_type: "stock" }, accessToken: token("a"), sessionRevision: 1, loaded: true }
  const slots = [], effects = [], subscriptions = [], calls = []
  let cursor = 0
  const dependencies = {
    react: {
      useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial; return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value }] },
      useRef(initial) { const index = cursor++; return slots[index] ??= { current: initial } },
      useEffect(callback, deps) { const index = cursor++; if (!(index in slots) || deps.some((value, position) => value !== slots[index].deps[position])) { slots[index]?.cleanup?.(); slots[index] = { deps }; effects.push(() => { slots[index].cleanup = callback() }) } },
    },
    "@/lib/ai-client-scope": { captureAiScope },
    "@/components/market/stock-eod-gap-browser": { StockEodGapBrowser: GapBrowser },
    "@/components/market/stock-archive-catalog": { StockArchiveCatalog: ArchiveCatalog },
    "@/stores/auth": { useAuthStore: Object.assign((selector) => selector(auth), { getState: () => auth, subscribe: (callback) => { subscriptions.push(callback); return () => {} } }) },
    "@/lib/stock-data-availability-api": { parseAvailabilityPeriod, getStockDataAvailability: (period, credential, signal) => new Promise((resolve, reject) => calls.push({ period, credential, signal, resolve, reject })) },
  }
  const module = { exports: {} }
  const source = readFileSync(new URL("./stock-data-availability.tsx", import.meta.url), "utf8")
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText, { module, exports: module.exports, Error, AbortController, Set, require: (name) => dependencies[name] ?? requireModule(name) })
  return { calls, render() { cursor = 0; return module.exports.StockDataAvailability({ refreshKey: 0 }) }, async settle() { while (effects.length) effects.shift()(); await new Promise(setImmediate) }, change(partial) { const previous = auth; auth = { ...auth, ...partial }; subscriptions.forEach((callback) => callback(auth, previous)) }, unmount() { slots.forEach((slot) => slot?.cleanup?.()) } }
}

test("distinct coverage layers disclose gaps, worker unavailability, registered versions and bounded dataset list", async () => {
  const page = mount(); page.render(); await page.settle(); page.calls[0].resolve(payload); await page.settle()
  const content = text(page.render())
  for (const expected of [/证券目录/, /3 只/, /2 \/ 3/, /历史观测/, /100 条/, /缺价 20/, /不保证研究成功/, /fixture-worker离线/, /最多100/, /version-0/, /600003/, /600009/, /前复权/, /未复权/]) assert.match(content, expected)
  assert.equal(elements(page.render()).filter((item) => item.type === "li" && text(item).includes("fixture-replay")).length, 20)
  elements(page.render()).find((item) => item.type === "button" && text(item) === "显示更多数据集").props.onClick()
  assert.equal(elements(page.render()).filter((item) => item.type === "li" && text(item).includes("fixture-replay")).length, 40)
  assert.ok(elements(page.render()).some((item) => item.type === "a" && item.props.href === "/factor-research"))
})

test("date drafts require explicit apply and invalid windows never call the API", async () => {
  const page = mount(); page.render(); await page.settle(); page.calls[0].resolve(payload); await page.settle()
  elements(page.render()).find((item) => item.props?.["aria-label"] === "覆盖开始日期").props.onChange({ target: { value: "2026-10-01" } })
  page.render(); await page.settle(); assert.equal(page.calls.length, 1)
  elements(page.render()).find((item) => item.type === "form").props.onSubmit({ preventDefault() {} })
  page.render(); await page.settle(); assert.equal(page.calls[1].period.start_date, "2026-10-01")
  elements(page.render()).find((item) => item.props?.["aria-label"] === "覆盖结束日期").props.onChange({ target: { value: "2026-09-01" } })
  elements(page.render()).find((item) => item.type === "form").props.onSubmit({ preventDefault() {} })
  assert.match(text(page.render()), /开始不得晚于结束/); assert.equal(page.calls.length, 2)
  elements(page.render()).find((item) => item.type === "button" && text(item) === "重置推荐区间").props.onClick()
  page.render(); await page.settle(); assert.deepEqual({ ...page.calls.at(-1).period }, {})
})

test("partial and total failures remain errors with retry and are not displayed as zero coverage", async () => {
  const page = mount(); page.render(); await page.settle()
  page.calls[0].resolve({ ...payload, market: { state: "failed", message: "fixture-market-failed", request_id: "fixture-request" }, replay: ready({ enabled: false, datasets: [] }) }); await page.settle()
  const content = text(page.render()); assert.match(content, /fixture-market-failed/); assert.match(content, /fixture-request/); assert.match(content, /回放未启用/); assert.doesNotMatch(content, /证券目录 0/)
  elements(page.render()).find((item) => item.type === "button" && text(item) === "重试已应用区间").props.onClick()
  page.render(); await page.settle(); page.calls.at(-1).reject(new Error("fixture-total-failure")); await page.settle()
  assert.match(text(page.render()), /fixture-total-failure/); assert.doesNotMatch(text(page.render()), /version-0/)
})

test("logout and market pending roundtrip cancel and reject late same-key responses", async () => {
  const page = mount(); page.render(); await page.settle()
  page.change({ marketSwitchPending: true }); page.change({ marketSwitchPending: false })
  assert.equal(page.calls[0].signal.aborted, true)
  page.calls[0].resolve(payload); await page.settle(); assert.doesNotMatch(text(page.render()), /version-0/)
  page.render(); await page.settle(); assert.equal(page.calls.at(-1).credential, token("a"))
  page.change({ user: null, accessToken: null, sessionRevision: 2 })
  page.calls.at(-1).resolve(payload); await page.settle(); assert.doesNotMatch(text(page.render()), /version-0/)
  page.unmount()
})

test("per-security EOD gaps only mount after explicit user selection", async () => {
  const page = mount(); page.render(); await page.settle(); page.calls[0].resolve(payload); await page.settle()
  assert.equal(elements(page.render()).some((item) => item.type === GapBrowser), false)
  elements(page.render()).find((item) => item.type === "button" && text(item) === "查看逐证券盘后缺口").props.onClick()
  assert.ok(elements(page.render()).some((item) => item.type === GapBrowser))
})

function ArchiveCatalog() { return null }
test("archive versions only mount after explicit selection and scope changes close them", async () => {
  const page = mount(); page.render(); await page.settle(); page.calls[0].resolve(payload); await page.settle()
  assert.equal(elements(page.render()).some((item) => item.type === ArchiveCatalog), false)
  elements(page.render()).find((item) => item.type === "button" && text(item) === "查看原价归档版本目录").props.onClick()
  assert.ok(elements(page.render()).some((item) => item.type === ArchiveCatalog))
  page.change({ marketSwitchPending: true })
  assert.equal(elements(page.render()).some((item) => item.type === ArchiveCatalog), false)
})
