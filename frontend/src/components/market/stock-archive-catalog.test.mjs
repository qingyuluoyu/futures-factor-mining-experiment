import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"
import { captureAiScope } from "../../lib/ai-client-scope.ts"

const settle = () => new Promise(setImmediate)
const payload = { snapshot_at: "2026-10-22T00:00:00Z", items: [{ batch_id: "qa-batch", version: "qa-version", source_data_version: "qa-source-version", catalog_version: "qa-catalog", method_version: "qa-method", trade_date: "2026-10-21", source_collected_at: "2026-10-21T09:00:00Z", archived_at: "2026-10-21T10:00:00Z", row_count: 2, expected_count: 3, missing_count: 1, unknown_count: 0 }], total: 21, has_more: true }
payload.membership_token = "d".repeat(32)
const elements = (tree) => Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : []
const text = (tree) => Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree)
function mount() {
  const token = `header.${Buffer.from(JSON.stringify({ sub: "a", trading_mode: "virtual" })).toString("base64url")}.signature`
  let auth = { user: { id: "a", market_type: "stock" }, accessToken: token, sessionRevision: 1, loaded: true }
  const slots = [], effects = [], subscriptions = [], calls = []
  let cursor = 0
  const dependencies = {
    react: {
      useState(initial) { const slot = cursor++; if (!(slot in slots)) slots[slot] = initial; return [slots[slot], (value) => { slots[slot] = typeof value === "function" ? value(slots[slot]) : value }] },
      useRef(initial) { const slot = cursor++; return slots[slot] ??= { current: initial } },
      useEffect(callback, values) { const slot = cursor++; if (!(slot in slots) || values.some((value, index) => value !== slots[slot].values[index])) { slots[slot]?.cleanup?.(); slots[slot] = { values }; effects.push(() => { slots[slot].cleanup = callback() }) } },
    },
    "@/lib/ai-client-scope": { captureAiScope },
    "@/components/market/stock-archive-observation": { StockArchiveObservation: Observation },
    "@/stores/auth": { useAuthStore: Object.assign((selector) => selector(auth), { getState: () => auth, subscribe: (callback) => { subscriptions.push(callback); return () => {} } }) },
    "@/lib/stock-archive-catalog-api": { getStockArchiveCatalog: (query, credential, signal) => new Promise((resolve, reject) => calls.push({ query, credential, signal, resolve, reject })) },
  }
  const module = { exports: {} }, nativeRequire = createRequire(import.meta.url)
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL("./stock-archive-catalog.tsx", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText, { module, exports: module.exports, AbortController, Error, Date, Intl, require: (name) => dependencies[name] ?? nativeRequire(name) })
  return { calls, render() { cursor = 0; const tree = module.exports.StockArchiveCatalog({ refreshKey: 0 }); effects.splice(0).forEach((effect) => effect()); return tree }, async ready() { await settle(); this.render(); await settle() }, change(patch) { const previous = auth; auth = { ...auth, ...patch }; subscriptions.forEach((callback) => callback(auth, previous)) }, unmount() { slots.forEach((slot) => slot?.cleanup?.()) } }
}
function button(page, label) { const result = elements(page.render()).find((item) => item.type === "button" && text(item) === label); assert.ok(result, label); return result }

test("version catalog displays provenance and qualification without enabling financial execution", async () => {
  const page = mount(); page.render(); await page.ready(); page.calls[0].resolve(payload); await page.ready()
  const content = text(page.render())
  for (const expected of [/qa-version/, /qa-source-version/, /qa-catalog/, /2 \/ 3/, /缺失 1/, /不是.*认证/, /不能.*回测|不能.*撮合/, /未复权/, /100股/]) assert.match(content, expected)
  assert.equal(page.calls.length, 1)
  assert.equal(elements(page.render()).some((item) => item.type === "a" && /run|trading/.test(item.props.href)), false)
})

test("pagination pins the returned snapshot while explicit refresh opens a new snapshot", async () => {
  const page = mount(); page.render(); await page.ready(); page.calls[0].resolve(payload); await page.ready()
  button(page, "下一页归档版本").props.onClick(); await page.ready()
  assert.equal(page.calls[1].query.offset, 20)
  assert.equal(page.calls[1].query.snapshot_at, payload.snapshot_at)
  assert.equal(page.calls[1].query.membership_token, payload.membership_token)
  page.calls[1].resolve({ ...payload, has_more: false }); await page.ready()
  button(page, "刷新归档版本").props.onClick(); await page.ready()
  assert.equal(page.calls[2].query.offset, 0)
  assert.equal(page.calls[2].query.snapshot_at, undefined)
  assert.equal(page.calls[2].query.membership_token, undefined)
})

test("explicit date drafts and errors do not fabricate empty success or auto retry", async () => {
  const page = mount(); page.render(); await page.ready(); page.calls[0].reject(new Error("qa-unavailable")); await page.ready()
  assert.match(text(page.render()), /qa-unavailable/)
  assert.doesNotMatch(text(page.render()), /共 0|暂无归档/)
  const input = elements(page.render()).find((item) => item.props?.["aria-label"] === "归档交易日期")
  input.props.onChange({ target: { value: "2026-02-30" } }); page.render()
  elements(page.render()).find((item) => item.type === "form").props.onSubmit({ preventDefault() {} }); await page.ready()
  assert.equal(page.calls.length, 1)
  input.props.onChange({ target: { value: "2026-10-21" } }); page.render()
  elements(page.render()).find((item) => item.type === "form").props.onSubmit({ preventDefault() {} }); await page.ready()
  assert.equal(page.calls[1].query.trade_date, "2026-10-21")
})

test("pending roundtrip, token rotation and unmount suppress old catalog responses", async () => {
  for (const patch of [{ marketSwitchPending: true }, { accessToken: "rotated" }, { sessionRevision: 2 }, { user: { id: "a", market_type: "futures" } }]) {
    const page = mount(); page.render(); await page.ready()
    page.change(patch)
    if (patch.marketSwitchPending) page.change({ marketSwitchPending: false })
    assert.equal(page.calls[0].signal.aborted, true)
    page.calls[0].resolve(payload); await page.ready()
    assert.doesNotMatch(text(page.render()), /qa-version/)
    page.unmount()
    assert.equal(page.calls.at(-1).signal.aborted, true)
  }
})

test("membership change hides the invalid page and waits for explicit unpinned refresh", async () => {
  const page = mount(); page.render(); await page.ready(); page.calls[0].resolve(payload); await page.ready()
  button(page, "下一页归档版本").props.onClick(); await page.ready()
  page.calls[1].reject(new Error("归档目录成员已变化，请显式刷新")); await page.ready()
  assert.match(text(page.render()), /成员已变化/)
  assert.doesNotMatch(text(page.render()), /qa-version|共 0/)
  assert.equal(page.calls.length, 2)
  button(page, "刷新归档版本").props.onClick(); await page.ready()
  assert.equal(page.calls.length, 3)
  assert.equal(page.calls[2].query.offset, 0)
  assert.equal(page.calls[2].query.snapshot_at, undefined)
  assert.equal(page.calls[2].query.membership_token, undefined)
})

function Observation() { return null }
test("raw observation entry requires explicit version selection and refresh closes old selection", async () => {
  const page = mount(); page.render(); await page.ready(); page.calls[0].resolve(payload); await page.ready()
  assert.equal(elements(page.render()).some((item) => item.type === Observation), false)
  button(page, "核对本版本原价").props.onClick(); page.render()
  const detail = elements(page.render()).find((item) => item.type === Observation)
  assert.equal(detail.props.version, "qa-version")
  button(page, "刷新归档版本").props.onClick(); await page.ready()
  assert.equal(elements(page.render()).some((item) => item.type === Observation), false)
})
