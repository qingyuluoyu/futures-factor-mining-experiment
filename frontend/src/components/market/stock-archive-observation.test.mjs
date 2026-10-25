import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"
import { captureAiScope } from "../../lib/ai-client-scope.ts"

const version = "a".repeat(64)
const payload = { versions: [version], instrument_ids: ["600001.SH"], observations: [{ open: "12", close: "12.000000000000000002", high: "13", low: "11", pre_close: "12", volume: "0.01", amount: "456789", instrument_id: "600001.SH", trade_date: "2026-10-21" }], missing: [], batches: [{ verified_row_count: 5548, source_data_version: "qa-source-version", source_collected_at: "2026-10-21T09:00:00Z" }], content_sha256: "qa-content-version", generated_at: "2026-10-22T00:00:00Z" }
const settle = () => new Promise(setImmediate)
const elements = (tree) => Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : []
const text = (tree) => Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree)
function mount() {
  const token = `header.${Buffer.from(JSON.stringify({ sub: "a", trading_mode: "virtual" })).toString("base64url")}.signature`
  let auth = { user: { id: "a", market_type: "stock" }, accessToken: token, sessionRevision: 1, loaded: true }, currentVersion = version, cursor = 0
  const slots = [], effects = [], subscriptions = [], calls = []
  const dependencies = {
    react: {
      useState(initial) { const slot = cursor++; if (!(slot in slots)) slots[slot] = initial; return [slots[slot], (value) => { slots[slot] = typeof value === "function" ? value(slots[slot]) : value }] },
      useRef(initial) { const slot = cursor++; return slots[slot] ??= { current: initial } },
      useEffect(callback, values) { const slot = cursor++; if (!(slot in slots) || values.some((value, index) => value !== slots[slot].values[index])) { slots[slot]?.cleanup?.(); slots[slot] = { values }; effects.push(() => { slots[slot].cleanup = callback() }) } },
    },
    "@/lib/ai-client-scope": { captureAiScope },
    "@/stores/auth": { useAuthStore: Object.assign((selector) => selector(auth), { getState: () => auth, subscribe: (callback) => { subscriptions.push(callback); return () => {} } }) },
    "@/lib/stock-archive-observation-api": { getStockArchiveObservation: (hash, instrument, credential, signal) => new Promise((resolve, reject) => calls.push({ hash, instrument, credential, signal, resolve, reject })) },
  }
  const module = { exports: {} }, nativeRequire = createRequire(import.meta.url)
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL("./stock-archive-observation.tsx", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText, { module, exports: module.exports, AbortController, Error, require: (name) => dependencies[name] ?? nativeRequire(name) })
  return { calls, render() { cursor = 0; const tree = module.exports.StockArchiveObservation({ version: currentVersion }); effects.splice(0).forEach((effect) => effect()); return tree }, async ready() { await settle(); this.render(); await settle() }, change(patch) { const previous = auth; auth = { ...auth, ...patch }; subscriptions.forEach((callback) => callback(auth, previous)) }, version(value) { currentVersion = value; this.render() }, unmount() { slots.forEach((slot) => slot?.cleanup?.()) } }
}
function fill(page, value) { elements(page.render()).find((item) => item.props?.["aria-label"] === "核对标准证券标识").props.onChange({ target: { value } }); page.render() }
function submit(page) { return elements(page.render()).find((item) => item.type === "form").props.onSubmit({ preventDefault() {} }) }

test("opening a version reads nothing until explicit valid instrument submission and keeps decimal text", async () => {
  const page = mount(); page.render(); await page.ready(); assert.equal(page.calls.length, 0)
  fill(page, "600001"); submit(page); await page.ready(); assert.equal(page.calls.length, 0)
  fill(page, "600001.SH"); submit(page); submit(page); await page.ready()
  assert.equal(page.calls.length, 1)
  assert.equal(page.calls[0].hash, version)
  page.calls[0].resolve(payload); await page.ready()
  const content = text(page.render())
  for (const expected of [/0.01/, /12.000000000000000002/, /5548/, /同源/, /不是.*PIT|并非.*PIT/, /不能.*回测|不.*撮合/, /qa-source-version/]) assert.match(content, expected)
})

test("source missing remains a classified absence instead of zero prices or suspension", async () => {
  const page = mount(); page.render(); fill(page, "600001.SH"); submit(page); await page.ready()
  page.calls[0].resolve({ ...payload, observations: [], missing: [{ reason: "source_missing", instrument_id: "600001.SH", trade_date: "2026-10-21" }] }); await page.ready()
  assert.match(text(page.render()), /缺少|缺失/)
  assert.match(text(page.render()), /不代表停牌/)
  assert.doesNotMatch(text(page.render()), /0\.01|收盘价.*0/)
  assert.equal(elements(page.render()).some((item) => item.type === "table"), false)
})

test("errors stay visible and no automatic retry or fallback query is issued", async () => {
  const page = mount(); page.render(); fill(page, "600001.SH"); submit(page); await page.ready()
  page.calls[0].reject(new Error("qa-conflict")); await page.ready()
  assert.match(text(page.render()), /qa-conflict/)
  assert.doesNotMatch(text(page.render()), /全量核对.*5548/)
  assert.equal(page.calls.length, 1)
})

test("changed version session or market cannot display late raw prices", async () => {
  for (const patch of [{ version: "b".repeat(64) }, { marketSwitchPending: true }, { accessToken: "rotated" }, { sessionRevision: 2 }, { user: { id: "a", market_type: "futures" } }]) {
    const page = mount(); page.render(); fill(page, "600001.SH"); submit(page); await page.ready()
    if (patch.version) page.version(patch.version)
    else page.change(patch)
    if (patch.marketSwitchPending) page.change({ marketSwitchPending: false })
    assert.equal(page.calls[0].signal.aborted, true)
    page.calls[0].resolve(payload); await page.ready()
    assert.doesNotMatch(text(page.render()), /0\.01|qa-source-version/)
    page.unmount()
  }
})
