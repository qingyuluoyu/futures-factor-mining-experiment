import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"
import * as api from "../../lib/stock-research-api.ts"

const id = "11111111-1111-4111-8111-111111111111"
const params = { factor_id: "ret_20d", start_date: "20260713", end_date: "20260911", horizon: 7, groups: 10, preprocess: { winsorize: "none", standardize: false, fillna: false, industry_neutral: true, mktcap_neutral: true } }
const sourceJob = { id, kind: "analysis", method_version: "saved-v0", workflow_version: "old-v0", params }
const tokenFor = (user, mode) => `header.${Buffer.from(JSON.stringify({ sub: user, trading_mode: mode })).toString("base64url")}.signature`
const token = tokenFor("user", "virtual")
const settle = () => new Promise((resolve) => setImmediate(resolve))

function mount({ query = id, job = sourceJob, failure = null, deferred = null, blocked = false, catalog = [{ id: "ret_20d" }], catalogLoading = false, expectedKind } = {}) {
  const source = readFileSync(new URL("./research-restore-panel.tsx", import.meta.url), "utf8")
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const slots = [], effects = [], calls = { get: [], apply: [] }
  let cursor = 0
  const controls = { query, job, failure, deferred, blocked, catalog, catalogLoading, expectedKind, auth: { user: { id: "user", market_type: "stock" }, accessToken: token, sessionRevision: 1 } }
  const dependencies = {
    react: {
      useState(initial) { const position = cursor++; if (!(position in slots)) slots[position] = initial; return [slots[position], (value) => { slots[position] = typeof value === "function" ? value(slots[position]) : value }] },
      useRef(initial) { const position = cursor++; if (!(position in slots)) slots[position] = { current: initial }; return slots[position] },
      useEffect(callback, deps) { const position = cursor++; if (!(position in slots) || deps.some((value, index) => value !== slots[position].deps[index])) { slots[position]?.cleanup?.(); slots[position] = { deps }; effects.push(() => { slots[position].cleanup = callback() }) } },
    },
    "next/navigation": { useSearchParams: () => new URLSearchParams(controls.query === null ? "" : `restore=${controls.query}`) },
    "@/stores/auth": { useAuthStore: Object.assign((selector) => selector(controls.auth), { getState: () => controls.auth }) },
    "@/lib/stock-research-api": { ...api, getStockResearchJob: async (requested, captured, signal) => { calls.get.push({ requested, captured, signal }); if (controls.failure) throw controls.failure; return controls.deferred ?? controls.job } },
  }
  const module = { exports: {} }, requireModule = createRequire(import.meta.url)
  vm.runInNewContext(output, { module, exports: module.exports, require: (name) => dependencies[name] ?? requireModule(name), AbortController, Error })
  const render = () => { cursor = 0; const tree = module.exports.ResearchRestorePanel({ token, scope: "user:virtual", factors: controls.catalog, catalogLoading: controls.catalogLoading, expectedKind: controls.expectedKind, blocked: controls.blocked, onApply: (draft) => calls.apply.push(draft) }); effects.splice(0).forEach((callback) => callback()); return tree }
  render()
  return { render, controls, calls, async ready() { await settle(); render(); await settle() }, unmount() { slots.forEach((slot) => slot?.cleanup?.()) } }
}
function elements(tree) { if (Array.isArray(tree)) return tree.flatMap(elements); return tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : [] }
function text(tree) { if (Array.isArray(tree)) return tree.map(text).join(""); return tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }
function applyButton(page) { const button = elements(page.render()).find((element) => element.type === "button" && text(element).includes("应用到新研究表单")); assert.ok(button); return button }

test("history configuration loads under captured identity and requires explicit apply without old idempotency", async () => {
  const page = mount(); await page.ready()
  assert.equal(page.calls.get.length, 1, "restore query must load the saved job")
  assert.equal(page.calls.get[0].requested, id)
  assert.equal(page.calls.get[0].captured, token)
  assert.equal(page.calls.apply.length, 0)
  assert.match(text(page.render()), /saved-v0/)
  assert.match(text(page.render()), /旧方法|历史方法/)
  assert.match(text(page.render()), /PIT/)
  applyButton(page).props.onClick()
  assert.equal(page.calls.apply.length, 1)
  assert.equal(page.calls.apply[0].kind, "analysis")
  assert.deepEqual(JSON.parse(JSON.stringify(page.calls.apply[0].parameters)), params)
  assert.equal("idempotency_key" in page.calls.apply[0], false)
  assert.match(text(page.render()), /已应用/)
})

test("invalid query is rejected before owner endpoint and missing query has no restore panel", async () => {
  const invalid = mount({ query: "not-a-uuid" }); await invalid.ready()
  assert.match(text(invalid.render()), /无效/)
  assert.equal(invalid.calls.get.length, 0)
  const absent = mount({ query: null }); await absent.ready()
  assert.equal(absent.render(), null)
  assert.equal(absent.calls.get.length, 0)
})

test("loading, inaccessible and failed requests expose no applyable stale configuration", async () => {
  let finish
  const loading = mount({ deferred: new Promise((resolve) => { finish = resolve }) })
  assert.match(text(loading.render()), /加载/)
  loading.unmount(); finish(sourceJob); await settle()
  for (const failure of [Object.assign(new Error("研究不存在"), { status: 404 }), new Error("连接失败")]) {
    const page = mount({ failure }); await page.ready()
    assert.match(text(page.render()), /不存在|连接失败/)
    assert.equal(elements(page.render()).some((element) => element.type === "button" && text(element).includes("应用到新研究表单")), false)
  }
})

test("invalid saved schema and unavailable factors cannot fill a fresh draft", async () => {
  for (const job of [{ ...sourceJob, params: { ...params, horizon: 0 } }, { ...sourceJob, params: { ...params, extra_control: "injected" } }, { ...sourceJob, params: { ...params, factor_id: "retired_factor" } }]) {
    const page = mount({ job }); await page.ready()
    assert.match(text(page.render()), /参数|因子/)
    assert.equal(applyButton(page).props.disabled, true)
    applyButton(page).props.onClick()
    assert.equal(page.calls.apply.length, 0)
  }
})

test("busy or unknown submission blocks applying and current catalog is revalidated on click", async () => {
  const page = mount({ blocked: true }); await page.ready()
  assert.equal(applyButton(page).props.disabled, true)
  applyButton(page).props.onClick(); assert.equal(page.calls.apply.length, 0)
  page.controls.blocked = false
  page.controls.catalog = [{ id: "new_factor" }]
  applyButton(page).props.onClick(); assert.equal(page.calls.apply.length, 0)
  assert.match(text(page.render()), /因子/)
})

test("changed owner, JWT mode and token prevent late configuration and stale apply events", async () => {
  for (const auth of [{ user: { id: "other" }, accessToken: tokenFor("other", "virtual") }, { user: { id: "user" }, accessToken: tokenFor("user", "live") }, { user: { id: "user" }, accessToken: `${token}renewed` }]) {
    let finish
    const page = mount({ deferred: new Promise((resolve) => { finish = resolve }) })
    page.controls.auth = auth
    finish(sourceJob); await page.ready()
    assert.doesNotMatch(text(page.render()), /saved-v0/)
    assert.equal(page.calls.apply.length, 0)
    const loaded = mount(); await loaded.ready()
    const oldClick = applyButton(loaded).props.onClick
    loaded.controls.auth = auth
    oldClick(); assert.equal(loaded.calls.apply.length, 0)
  }
})

test("same-token new session and market change reject late restore and stale apply events", async () => {
  for (const changed of [{ sessionRevision: 2 }, { user: { id: "user", market_type: "futures" } }]) {
    let finish
    const page = mount({ deferred: new Promise((resolve) => { finish = resolve }) })
    page.controls.auth = { ...page.controls.auth, ...changed }
    finish(sourceJob); await page.ready()
    assert.doesNotMatch(text(page.render()), /saved-v0/)
    assert.equal(page.calls.apply.length, 0)
    const loaded = mount(); await loaded.ready()
    const oldClick = applyButton(loaded).props.onClick
    loaded.controls.auth = { ...loaded.controls.auth, ...changed }
    oldClick(); assert.equal(loaded.calls.apply.length, 0)
  }
})

test("superseded source requests abort and cannot replace a newer source", async () => {
  let finish
  const page = mount({ deferred: new Promise((resolve) => { finish = resolve }) })
  assert.equal(page.calls.get.length, 1, "restore query must start the source request")
  const old = page.calls.get[0]
  page.controls.query = "22222222-2222-4222-8222-222222222222"
  page.controls.deferred = null
  page.controls.job = { ...sourceJob, id: page.controls.query, method_version: "new-source" }
  page.render(); await page.ready()
  assert.equal(old.signal.aborted, true)
  finish(sourceJob); await page.ready()
  assert.match(text(page.render()), /new-source/)
  assert.doesNotMatch(text(page.render()), /saved-v0/)
})

test("catalog loading gates configuration until current factors are known", async () => {
  const page = mount({ catalogLoading: true }); await page.ready()
  assert.equal(applyButton(page).props.disabled, true)
  applyButton(page).props.onClick(); assert.equal(page.calls.apply.length, 0)
  page.controls.catalogLoading = false
  assert.equal(applyButton(page).props.disabled, false)
  applyButton(page).props.onClick(); assert.equal(page.calls.apply.length, 1)
})

test("a response with a different source ID cannot apply saved parameters", async () => {
  const page = mount({ job: { ...sourceJob, id: "22222222-2222-4222-8222-222222222222" } }); await page.ready()
  assert.match(text(page.render()), /不匹配/)
  assert.equal(elements(page.render()).some((element) => element.type === "button" && text(element).includes("应用到新研究表单")), false)
})

test("a dedicated page directs other task kinds to their correct page without applying parameters", async () => {
  const page = mount({ expectedKind: "backtest" }); await page.ready()
  const tree = page.render()
  assert.match(text(tree), /属于因子检验/)
  assert.ok(elements(tree).some((node) => node.props?.href === `/factor-research?restore=${id}`))
  assert.equal(elements(tree).some((node) => node.type === "button" && text(node).includes("应用到新研究表单")), false)
  assert.equal(page.calls.apply.length, 0)
})

test("changing page purpose prevents an already captured restore handler from applying a different kind", async () => {
  const page = mount({ expectedKind: "analysis" }); await page.ready()
  const captured = applyButton(page).props.onClick
  page.controls.expectedKind = "backtest"
  page.render()
  captured()
  assert.equal(page.calls.apply.length, 0)
})

test("a portfolio restore remains on its dedicated page and preserves the saved proportional fees", async () => {
  const job = { ...sourceJob, kind: "backtest", params: { start_date: "20260713", end_date: "20260911", factor_ids: ["ret_20d"], rebalance: "month", top_n: 15, weight: "equal", commission_rate: 0.0003, slippage_rate: 0.001, preprocess: params.preprocess } }
  const wrong = mount({ expectedKind: "analysis", job }); await wrong.ready()
  assert.match(text(wrong.render()), /属于组合回测/)
  assert.ok(elements(wrong.render()).some((node) => node.props?.href === `/backtest?restore=${id}`))
  assert.equal(wrong.calls.apply.length, 0)
  const page = mount({ expectedKind: "backtest", job }); await page.ready()
  applyButton(page).props.onClick()
  assert.equal(page.calls.apply.length, 1)
  assert.equal(page.calls.apply[0].kind, "backtest")
  assert.equal(page.calls.apply[0].parameters.commission_rate, 0.0003)
  assert.equal(page.calls.apply[0].parameters.slippage_rate, 0.001)
  assert.equal(page.calls.get.length, 1)
})
