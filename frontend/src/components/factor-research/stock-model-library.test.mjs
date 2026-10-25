import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"

const requireModule = createRequire(import.meta.url)
const id = "b6372fe1-e9ee-4d12-b09c-929922f1d7e3", sha = "a".repeat(64)
const summary = { id, dataset: { dataset_id: id, data_version: sha, qualification: "observation_only" }, state: "ready", validation_status: "qa_only", created_at: "2026-10-25T01:00:00Z", fold_count: 1, feature_names: ["quality"], holdout_registry_verified: false }
const parameters = { n_estimators: 10, max_depth: 3, num_leaves: 7, min_child_samples: 5, learning_rate: 0.05, reg_alpha: 0, reg_lambda: 0, seed: 20261025, threads: 2, max_seconds: 120 }
const detail = { ...summary, parameters, folds: [{ fold_id: sha, split: { train_start: "2025-01-01", train_end: "2025-12-31", validation_start: "2026-01-01", validation_end: "2026-01-30", test_start: "2026-02-01", test_end: "2026-02-28" }, model: { model_sha256: sha, runtime: { lightgbm: "4.7.0" }, training_knowledge_cutoff: "2026-01-29", method_version: "stock-native-lambdarank-v1", feature_order: ["quality"], feature_versions: { quality: sha }, feature_gain: { quality: 1.5 }, trees_used: 10, elapsed_seconds: 0.5 } }], report_sha256: sha, limitations: ["QA数据，未经认证"] }
const settle = () => new Promise((resolve) => setImmediate(resolve))
function mount({ enabled = true, total = 1 } = {}) {
  const slots = [], effects = [], calls = []; let cursor = 0
  const props = { token: "token-one", scope: "owner:virtual" }
  const controls = { enabled, total, failure: null, deferred: null, emptyLater: false, quarantined: false, authRevision: 1, authMarket: "stock" }
  const invoke = async (kind, value, token, signal, scope) => {
    calls.push({ kind, value, token, signal, scope })
    if (controls.failure?.kind === kind) throw Object.assign(new Error("暂时不可读"), controls.failure)
    if (controls.deferred?.kind === kind) return controls.deferred.promise
    if (kind === "availability") return { listing_enabled: controls.enabled, training_enabled: false, reason: "本人模型历史未开放" }
    if (kind === "list") return { items: controls.emptyLater && value.offset > 0 ? [] : [controls.quarantined ? { ...summary, state: "quarantined" } : summary], total: controls.total, has_more: controls.total > value.offset + 20 }
    if (kind === "detail") return controls.quarantined ? { ...detail, state: "quarantined" } : detail
    return { kind: "stock-walk-forward-calculation-v1", report: { evaluation: null, private_note: "本人只读报告" } }
  }
  const dependencies = {
    react: {
      useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === "function" ? initial() : initial; return [slots[i], (v) => { slots[i] = typeof v === "function" ? v(slots[i]) : v }] },
      useRef(initial) { const i = cursor++; if (!(i in slots)) slots[i] = { current: initial }; return slots[i] },
      useEffect(callback, deps) { const i = cursor++; if (!(i in slots) || deps.some((v, n) => v !== slots[i].deps[n])) { slots[i]?.cleanup?.(); slots[i] = { deps, callback }; effects.push(() => { slots[i].cleanup = callback() }) } },
    },
    "@/lib/stock-ml-models-api": {
      getStockModelAvailability: (token, signal) => invoke("availability", null, token, signal),
      listStockModels: (query, token, signal) => invoke("list", query, token, signal),
      getStockModel: (id, token, signal, scope) => invoke("detail", id, token, signal, scope),
      getStockModelReport: (id, token, signal) => invoke("report", id, token, signal),
    },
    "@/lib/stock-research-api": { stockResearchScope: () => props.scope },
    "@/stores/auth": { useAuthStore: { getState: () => ({ loaded: true, accessToken: props.token, sessionRevision: controls.authRevision, user: { id: props.scope.split(":")[0], market_type: controls.authMarket } }) } },
  }
  const target = new URL("./stock-model-library.tsx", import.meta.url)
  const source = existsSync(target) ? readFileSync(target, "utf8") : "export const StockModelLibrary = undefined"
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const module = { exports: {} }
  vm.runInNewContext(output, { module, exports: module.exports, AbortController, Error, TypeError, require: (name) => dependencies[name] ?? requireModule(name) })
  assert.equal(typeof module.exports.StockModelLibrary, "function", "model library component exists")
  const render = () => { cursor = 0; const tree = module.exports.StockModelLibrary(props); effects.splice(0).forEach((effect) => effect()); return tree }
  const ready = async () => { for (let i = 0; i < 4; i++) { render(); await settle() } }
  render()
  return { render, ready, calls, controls, props, replayEffects: () => slots.filter((slot) => slot?.callback).forEach((slot) => { slot.cleanup?.(); slot.cleanup = slot.callback() }), unmount: () => slots.forEach((slot) => slot?.cleanup?.()) }
}
function nodes(tree) { return Array.isArray(tree) ? tree.flatMap(nodes) : tree && typeof tree === "object" ? [tree, ...nodes(tree.props?.children)] : [] }
function text(tree) { return Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }
function button(page, label) { const found = nodes(page.render()).find((n) => n.type === "button" && text(n) === label); assert.ok(found, label); return found }
function hasButton(page, label) { return nodes(page.render()).some((n) => n.type === "button" && text(n) === label) }
async function openDetail(page) { button(page, "查看模型详情").props.onClick(); await page.ready() }

test("disabled capability is explicit and never presents zero models or requests list/training", async () => {
  const page = mount({ enabled: false }); await page.ready()
  assert.match(text(page.render()), /未开放/)
  assert.doesNotMatch(text(page.render()), /共 0|已存 0/)
  assert.deepEqual(page.calls.map((call) => call.kind), ["availability"])
  page.controls.enabled = true
  button(page, "重新读取本人模型").props.onClick(); await page.ready()
  assert.equal(page.calls.at(-1).kind, "list")
  page.unmount()
})
test("native details expose dates, parameters, versions, gains and limitations before explicit report read", async () => {
  const page = mount(); await page.ready(); await openDetail(page)
  const rendered = text(page.render())
  for (const expected of ["LightGBM", "4.7.0", "2026-02-01", "2026-02-28", "quality", "1.5", "n_estimators", "未封存", "QA", "普通聊天", "概率"]) assert.ok(rendered.includes(expected), expected)
  assert.equal(page.calls.filter((c) => c.kind === "report").length, 0)
  assert.equal(page.calls.find((c) => c.kind === "detail").scope, "owner:virtual")
  button(page, "读取已存计算报告").props.onClick(); await page.ready()
  assert.match(text(page.render()), /本人只读报告/)
  assert.equal(page.calls.filter((c) => c.kind === "report").length, 1)
  page.unmount()
})
test("503 requires an explicit retry and preserves the requested page offset", async () => {
  const page = mount({ total: 41 }); await page.ready()
  page.controls.failure = { kind: "list", status: 503, retryable: true }
  button(page, "模型下一页").props.onClick(); await page.ready()
  assert.equal(page.calls.filter((c) => c.kind === "list").length, 2)
  assert.ok(hasButton(page, "重试原请求"))
  page.controls.failure = null
  button(page, "重试原请求").props.onClick(); await page.ready()
  assert.equal(page.calls.filter((c) => c.kind === "list").at(-1).value.offset, 20)
  assert.match(text(page.render()), /21/)
  page.unmount()
})
test("an empty later page has an explicit first-page recovery without abandoning the visible range", async () => {
  const page = mount({ total: 41 }); await page.ready()
  page.controls.emptyLater = true; page.controls.total = 1
  button(page, "模型下一页").props.onClick(); await page.ready()
  assert.match(text(page.render()), /本页暂无模型/)
  button(page, "返回模型第一页").props.onClick(); await page.ready()
  assert.equal(page.calls.filter((c) => c.kind === "list").at(-1).value.offset, 0)
  page.unmount()
})
for (const status of [401, 403, 404]) test(`${status} clears old details/report and is never offered retry of that request`, async () => {
  const page = mount(); await page.ready(); await openDetail(page)
  button(page, "读取已存计算报告").props.onClick(); await page.ready()
  page.controls.failure = { kind: "report", status, retryable: false }
  button(page, "读取已存计算报告").props.onClick(); await page.ready()
  assert.doesNotMatch(text(page.render()), /本人只读报告|4\.7\.0/)
  assert.equal(hasButton(page, "重试原请求"), false)
  page.unmount()
})
for (const status of [400, 409, 422]) test(`${status} on report retains model evidence but does not repeat a deterministic request`, async () => {
  const page = mount(); await page.ready(); await openDetail(page)
  page.controls.failure = { kind: "report", status, retryable: true }
  button(page, "读取已存计算报告").props.onClick(); await page.ready()
  assert.match(text(page.render()), /4\.7\.0/)
  assert.equal(hasButton(page, "重试原请求"), false)
  page.unmount()
})
test("close detail aborts report transport and nonce rejects its late reply", async () => {
  const page = mount(); await page.ready(); await openDetail(page)
  let resolve
  page.controls.deferred = { kind: "report", promise: new Promise((done) => { resolve = done }) }
  button(page, "读取已存计算报告").props.onClick(); await settle()
  const call = page.calls.at(-1)
  button(page, "关闭模型详情").props.onClick()
  assert.equal(call.signal.aborted, true)
  resolve({ kind: "stock-walk-forward-calculation-v1", report: { secret: "晚到私有报告" } }); await page.ready()
  assert.doesNotMatch(text(page.render()), /晚到私有报告|4\.7\.0/)
  page.unmount()
})
test("scope/token changes clear private data synchronously, abort old work and do not auto-read the new user", async () => {
  const page = mount(); await page.ready(); await openDetail(page)
  let resolve
  page.controls.deferred = { kind: "report", promise: new Promise((done) => { resolve = done }) }
  button(page, "读取已存计算报告").props.onClick(); await settle()
  const before = page.calls.length, signal = page.calls.at(-1).signal
  page.props.scope = "other:live"; page.props.token = "token-two"
  assert.doesNotMatch(text(page.render()), /4\.7\.0/)
  await page.ready(); assert.equal(signal.aborted, true); assert.equal(page.calls.length, before)
  resolve({ kind: "stock-walk-forward-calculation-v1", report: { secret: "旧用户秘密" } }); await page.ready()
  assert.doesNotMatch(text(page.render()), /旧用户秘密/)
  page.controls.deferred = null
  button(page, "重新读取本人模型").props.onClick(); await page.ready()
  assert.equal(page.calls.at(-1).token, "token-two")
  page.unmount()
})
test("quarantined models keep history visible without exposing a report action", async () => {
  const page = mount(); page.controls.quarantined = true; await page.ready(); await openDetail(page)
  assert.match(text(page.render()), /隔离/)
  assert.equal(hasButton(page, "读取已存计算报告"), false)
  page.unmount()
})
test("React development effect replay aborts the first read and still loads the explicitly opened library", async () => {
  const page = mount(); page.replayEffects(); await page.ready()
  assert.equal(page.calls[0].signal.aborted, true)
  assert.equal(page.calls.filter((call) => call.kind === "list").length, 1)
  assert.ok(hasButton(page, "查看模型详情"))
  page.unmount()
})
test("unmount aborts the outstanding detail read", async () => {
  const page = mount(); await page.ready()
  let resolve
  page.controls.deferred = { kind: "detail", promise: new Promise((done) => { resolve = done }) }
  button(page, "查看模型详情").props.onClick(); await settle()
  const signal = page.calls.at(-1).signal
  page.unmount(); assert.equal(signal.aborted, true)
  resolve(detail); await settle()
})
test("store revision or market invalidation blocks a late report even before parent rerender", async () => {
  for (const change of [(controls) => { controls.authRevision++ }, (controls) => { controls.authMarket = "futures" }]) {
    const page = mount(); await page.ready(); await openDetail(page)
    let resolve
    page.controls.deferred = { kind: "report", promise: new Promise((done) => { resolve = done }) }
    button(page, "读取已存计算报告").props.onClick(); await settle()
    change(page.controls)
    resolve({ kind: "stock-walk-forward-calculation-v1", report: { secret: "旧会话晚到" } }); await page.ready()
    assert.doesNotMatch(text(page.render()), /旧会话晚到/)
    const count = page.calls.length
    const control = nodes(page.render()).find((node) => node.type === "button" && text(node) === "重新读取本人模型")
    control?.props.onClick(); await page.ready()
    assert.equal(page.calls.length, count)
    page.unmount()
  }
})
