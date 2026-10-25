import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import vm from "node:vm"
import test from "node:test"
import ts from "typescript"
import { parseStockScreenDraft } from "../../lib/stock-screen-api.ts"
import { captureAiScope } from "../../lib/ai-client-scope.ts"

const requireModule = createRequire(import.meta.url)
const elements = (tree) => Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : []
const text = (tree) => Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree)
const token = (id) => `header.${Buffer.from(JSON.stringify({ sub: id, trading_mode: "virtual" })).toString("base64url")}.signature`

function mount() {
  let auth = { user: { id: "a", market_type: "stock" }, accessToken: token("a"), sessionRevision: 1, loaded: true }
  const slots = [], effects = [], subscriptions = [], calls = [], navigation = []
  let cursor = 0, refreshKey = 0
  const dependencies = {
    react: {
      useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial; return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value }] },
      useRef(initial) { const index = cursor++; return slots[index] ??= { current: initial } },
      useEffect(callback, deps) { const index = cursor++; if (!(index in slots) || deps.some((value, position) => value !== slots[index].deps[position])) { slots[index]?.cleanup?.(); slots[index] = { deps }; effects.push(() => { slots[index].cleanup = callback() }) } },
    },
    "@/lib/ai-client-scope": { captureAiScope },
    "@/stores/auth": { useAuthStore: Object.assign((selector) => selector(auth), { getState: () => auth, subscribe: (callback) => { subscriptions.push(callback); return () => {} } }) },
    "@/lib/stock-screen-api": { parseStockScreenDraft, getStockScreenApi: (filters, signal) => new Promise((resolve, reject) => calls.push({ filters, signal, resolve, reject })) },
  }
  const module = { exports: {} }
  const source = readFileSync(new URL("./stock-screen-results.tsx", import.meta.url), "utf8")
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText, { module, exports: module.exports, Error, AbortController, require: (name) => dependencies[name] ?? requireModule(name) })
  return { calls, navigation, refresh() { refreshKey++ }, render() { cursor = 0; return module.exports.StockScreenResults({ industry: "银行", refreshKey, onSelect: (code) => navigation.push(code) }) }, async settle() { while (effects.length) effects.shift()(); await new Promise(setImmediate) }, change(partial) { const previous = auth; auth = { ...auth, ...partial }; subscriptions.forEach((callback) => callback(auth, previous)) } }
}

const payload = { screen_fingerprint: "a".repeat(64), total: 51, has_more: true, as_of: "2026-10-19", source: "fixture", volume_unit: "lot_100_shares", price_adjustment: "none", items: [{ code: "600001", name: "样本", instrument_id: "600001.SH", close: 10, change_pct: 0, volume: 0 }] }

test("draft conditions apply explicitly, invalid bounds do not query and pagination preserves applied conditions", async () => {
  const page = mount(); page.render(); await page.settle()
  page.calls[0].resolve(payload); await page.settle()
  let tree = page.render()
  elements(tree).find((item) => item.props?.["aria-label"] === "收盘价下限").props.onChange({ target: { value: "10" } })
  page.render(); await page.settle()
  assert.equal(page.calls.length, 1)
  elements(page.render()).find((item) => item.type === "form").props.onSubmit({ preventDefault() {} })
  page.render(); await page.settle()
  assert.equal(page.calls[1].filters.min_close, 10)
  page.calls[1].resolve(payload); await page.settle()
  elements(page.render()).find((item) => item.type === "button" && text(item) === "下一页").props.onClick()
  page.render(); await page.settle()
  assert.equal(page.calls[2].filters.offset, 50)
  assert.equal(page.calls[2].filters.min_close, 10)
  assert.equal(page.calls[2].filters.expected_fingerprint, payload.screen_fingerprint)
  page.calls[2].resolve({ ...payload, has_more: false }); await page.settle()
  elements(page.render()).find((item) => item.props?.["aria-label"] === "收盘价上限").props.onChange({ target: { value: "9" } })
  elements(page.render()).find((item) => item.type === "form").props.onSubmit({ preventDefault() {} })
  assert.match(text(page.render()), /下限|筛选/)
  assert.equal(page.calls.length, 3)
  elements(page.render()).find((item) => item.type === "button" && text(item).includes("样本")).props.onClick()
  assert.deepEqual(page.navigation, ["600001"])
})

test("scope change aborts requests, clears results and ignores a late old-account response", async () => {
  const page = mount(); page.render(); await page.settle()
  page.change({ user: { id: "b", market_type: "stock" }, accessToken: token("b"), sessionRevision: 2 })
  assert.equal(page.calls[0].signal.aborted, true)
  page.calls[0].resolve(payload); await page.settle()
  assert.doesNotMatch(text(page.render()), /样本/)
  await page.settle()
  assert.equal(page.calls.at(-1).filters.min_close, null)
})

test("market confirmation roundtrip rejects the old response and failures stay visible with retry", async () => {
  const page = mount(); page.render(); await page.settle()
  page.change({ marketSwitchPending: true }); page.change({ marketSwitchPending: false })
  page.calls[0].resolve(payload); await page.settle()
  assert.equal(page.calls[0].signal.aborted, true)
  assert.doesNotMatch(text(page.render()), /样本/)
  await page.settle()
  page.calls.at(-1).reject(new Error("后端未部署筛选入口")); await page.settle()
  assert.match(text(page.render()), /后端未部署/)
  elements(page.render()).find((item) => item.type === "button" && text(item).includes("重试已应用")).props.onClick()
  page.render(); await page.settle()
  page.calls.at(-1).resolve({ ...payload, total: 0, items: [], has_more: false }); await page.settle()
  assert.match(text(page.render()), /没有满足已应用条件/)
})

async function appliedSecondPage(page) {
  page.render(); await page.settle()
  page.calls.at(-1).resolve(payload); await page.settle()
  elements(page.render()).find((item) => item.props?.["aria-label"] === "收盘价下限").props.onChange({ target: { value: "10" } })
  elements(page.render()).find((item) => item.type === "form").props.onSubmit({ preventDefault() {} })
  page.render(); await page.settle()
  page.calls.at(-1).resolve(payload); await page.settle()
  elements(page.render()).find((item) => item.type === "button" && text(item) === "下一页").props.onClick()
  page.render(); await page.settle()
}

test("snapshot conflict explicitly reanchors applied filters without applying dirty draft or retrying old fingerprint", async () => {
  const page = mount(); await appliedSecondPage(page)
  elements(page.render()).find((item) => item.props?.["aria-label"] === "收盘价下限").props.onChange({ target: { value: "999" } })
  page.calls.at(-1).reject(Object.assign(new Error("行情快照已变化"), { status: 409 })); await page.settle()
  const tree = page.render()
  assert.match(text(tree), /快照.*变化/)
  assert.equal(elements(tree).some((item) => item.type === "button" && text(item) === "重试已应用条件"), false)
  const recover = elements(tree).find((item) => item.type === "button" && text(item) === "按已应用条件刷新第一页")
  assert.ok(recover, "snapshot conflicts need an explicit reanchor, preserving the actual applied filters")
  const count = page.calls.length
  recover.props.onClick(); page.render(); await page.settle()
  assert.equal(page.calls.length, count + 1)
  assert.equal(page.calls.at(-1).filters.min_close, 10)
  assert.equal(page.calls.at(-1).filters.industry, "银行")
  assert.equal(page.calls.at(-1).filters.offset, 0)
  assert.equal(page.calls.at(-1).filters.expected_fingerprint, null)
  assert.equal(elements(page.render()).find((item) => item.props?.["aria-label"] === "收盘价下限").props.value, "999")
})

test("visible applied conditions distinguish the active query from unapplied edits", async () => {
  const page = mount(); await appliedSecondPage(page)
  elements(page.render()).find((item) => item.props?.["aria-label"] === "收盘价下限").props.onChange({ target: { value: "999" } })
  const summary = elements(page.render()).find((item) => item.props?.["aria-label"] === "已应用筛选条件")
  assert.ok(summary, "the saved query must stay readable when the form contains a different draft")
  assert.match(text(summary), /银行/)
  assert.match(text(summary), /收盘价下限.*10/)
  assert.doesNotMatch(text(summary), /999/)
})

test("workspace refresh starts the applied query at its first page without sending the expired fingerprint", async () => {
  const page = mount(); await appliedSecondPage(page)
  const previous = page.calls.at(-1)
  const count = page.calls.length
  page.refresh(); page.render(); await page.settle(); page.render(); await page.settle()
  assert.equal(previous.signal.aborted, true)
  assert.equal(page.calls.length, count + 1, "refresh must not first send a doomed old-fingerprint query")
  assert.equal(page.calls.at(-1).filters.offset, 0)
  assert.equal(page.calls.at(-1).filters.expected_fingerprint, null)
  assert.equal(page.calls.at(-1).filters.min_close, 10)
  previous.resolve({ ...payload, items: [{ ...payload.items[0], name: "过期晚到" }] }); await page.settle()
  assert.doesNotMatch(text(page.render()), /过期晚到/)
})

test("empty later page offers first-page recovery while disclosing this is not an empty whole result", async () => {
  const page = mount(); await appliedSecondPage(page)
  page.calls.at(-1).resolve({ ...payload, items: [], total: 20, has_more: false }); await page.settle()
  const tree = page.render()
  assert.match(text(tree), /当前页没有/)
  assert.doesNotMatch(text(tree), /没有满足已应用条件的股票/)
  elements(tree).find((item) => item.type === "button" && text(item) === "按已应用条件刷新第一页").props.onClick()
  page.render(); await page.settle()
  assert.equal(page.calls.at(-1).filters.min_close, 10)
  assert.equal(page.calls.at(-1).filters.offset, 0)
})

test("temporary later-page read failure retries the pinned page without silently changing its snapshot", async () => {
  const page = mount(); await appliedSecondPage(page)
  const failed = page.calls.at(-1)
  failed.reject(Object.assign(new Error("短暂读取失败"), { status: 503 })); await page.settle()
  elements(page.render()).find((item) => item.type === "button" && text(item) === "重试已应用条件").props.onClick()
  page.render(); await page.settle()
  assert.deepEqual(page.calls.at(-1).filters, failed.filters)
  assert.equal(page.calls.at(-1).filters.expected_fingerprint, payload.screen_fingerprint)
  assert.equal(page.calls.at(-1).filters.offset, 50)
})

test("screen pagination stops at offset 10000 and discloses unshown results", async () => {
  const page = mount(); page.render(); await page.settle()
  for (let index = 0; index <= 200; index++) {
    page.calls.at(-1).resolve({ ...payload, total: 11000, has_more: true }); await page.settle()
    const next = elements(page.render()).find((item) => item.type === "button" && text(item) === "下一页")
    if (index === 200) {
      assert.equal(next.props.disabled, true)
      assert.match(text(page.render()), /分页上限.*未完整展示/)
      break
    }
    assert.equal(next.props.disabled, false)
    next.props.onClick(); page.render(); await page.settle()
  }
  assert.equal(page.calls.at(-1).filters.offset, 10000)
  assert.equal(page.calls.some((call) => call.filters.offset > 10000), false)
})
