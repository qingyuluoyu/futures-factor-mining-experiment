import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import vm from "node:vm"
import test from "node:test"
import ts from "typescript"
import { captureAiScope } from "../../lib/ai-client-scope.ts"
import { eodGapQuerySchema } from "../../lib/stock-eod-gaps-api.ts"

const requireModule = createRequire(import.meta.url)
const elements = (tree) => Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : []
const text = (tree) => Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree)
const token = (id, mode = "virtual") => `header.${Buffer.from(JSON.stringify({ sub: id, trading_mode: mode })).toString("base64url")}.signature`
const payload = { state: "ready", snapshot: { trade_date: "2024-01-03", provider: "fixture-source", source_uri: "https://teajoin.com/debug/api/daily", collected_at: "2024-01-03T09:00:00Z", data_version: "a".repeat(64), source_evidence_recorded: false }, totals: { missing: 51, unknown: 1 }, query_fingerprint: "b".repeat(64), total: 52, has_more: true, items: [{ code: "600102", name: "缺口股票", kind: "missing", catalog_status: "present_now", instrument_id: "600102.SH", instrument_id_basis: "current_catalog", history: { rows: 0, first_date: null, last_date: null } }, { code: "600104", name: null, kind: "unknown", catalog_status: "absent_now", instrument_id: "600104.SH", instrument_id_basis: "source_snapshot", history: { rows: 0, first_date: null, last_date: null } }] }

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
    "@/stores/auth": { useAuthStore: Object.assign((selector) => selector(auth), { getState: () => auth, subscribe: (callback) => { subscriptions.push(callback); return () => {} } }) },
    "@/lib/stock-eod-gaps-api": { eodGapQuerySchema, getStockEodGaps: (query, credential, signal) => new Promise((resolve, reject) => calls.push({ query, credential, signal, resolve, reject })) },
  }
  const module = { exports: {} }
  const source = readFileSync(new URL("./stock-eod-gap-browser.tsx", import.meta.url), "utf8")
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText, { module, exports: module.exports, Error, AbortController, require: (name) => dependencies[name] ?? requireModule(name) })
  return { calls, render() { cursor = 0; return module.exports.StockEodGapBrowser({ refreshKey: 0 }) }, async settle() { while (effects.length) effects.shift()(); await new Promise(setImmediate) }, change(partial) { const previous = auth; auth = { ...auth, ...partial }; subscriptions.forEach((callback) => callback(auth, previous)) } }
}

test("gap rows display source limits and zero observations, and pagination binds fingerprint", async () => {
  const page = mount(); page.render(); await page.settle(); page.calls[0].resolve(payload); await page.settle()
  const content = text(page.render())
  for (const expected of [/缺行情 51/, /快照时未匹配目录 1/, /0 条/, /2024-01-03/, /独立认证/, /不是停牌/, /当前目录/, /fixture-source/, /查询时/, /当时可得/]) assert.match(content, expected)
  const detailLinks = elements(page.render()).filter((item) => item.type === "a" && String(item.props.href).startsWith("/market"))
  assert.equal(detailLinks.length, 1); assert.equal(detailLinks[0].props.href, "/market?symbol=600102")
  elements(page.render()).find((item) => item.type === "button" && text(item) === "下一页缺口").props.onClick()
  page.render(); await page.settle()
  assert.equal(page.calls[1].query.offset, 50); assert.equal(page.calls[1].query.expected_fingerprint, payload.query_fingerprint)
})

test("drafts apply explicitly, refresh errors stay visible and retry does not silently drop the paging anchor", async () => {
  const page = mount(); page.render(); await page.settle(); page.calls[0].resolve(payload); await page.settle()
  elements(page.render()).find((item) => item.props?.["aria-label"] === "缺口代码或名称").props.onChange({ target: { value: "600104" } })
  page.render(); await page.settle(); assert.equal(page.calls.length, 1)
  elements(page.render()).find((item) => item.type === "form").props.onSubmit({ preventDefault() {} })
  page.render(); await page.settle(); assert.equal(page.calls[1].query.keyword, "600104"); assert.equal(page.calls[1].query.offset, 0)
  page.calls[1].reject(new Error("目录已变化，请重新应用")); await page.settle()
  assert.match(text(page.render()), /重新应用/); assert.doesNotMatch(text(page.render()), /缺口股票/)
  elements(page.render()).find((item) => item.type === "button" && text(item) === "重试缺口查询").props.onClick()
  page.render(); await page.settle(); assert.equal(page.calls[2].query.keyword, "600104")
  page.calls[2].resolve({ state: "disabled", snapshot: null, totals: null, items: [] }); await page.settle()
  assert.match(text(page.render()), /未启用/); assert.doesNotMatch(text(page.render()), /缺行情 0/)
})

test("mode switch and pending market roundtrip reject late old scope replies", async () => {
  const page = mount(); page.render(); await page.settle()
  page.change({ marketSwitchPending: true }); page.change({ marketSwitchPending: false })
  assert.equal(page.calls[0].signal.aborted, true)
  page.calls[0].resolve(payload); await page.settle(); assert.doesNotMatch(text(page.render()), /缺口股票/)
  page.render(); await page.settle()
  page.change({ accessToken: token("a", "live") })
  assert.equal(page.calls.at(-1).signal.aborted, true)
  page.calls.at(-1).resolve(payload); await page.settle(); assert.doesNotMatch(text(page.render()), /缺口股票/)
})
