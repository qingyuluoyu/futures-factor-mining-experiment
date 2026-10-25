import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"
import { loadPendingWrites } from "../../lib/stock-pending-writes-harness.mjs"

const requireModule = createRequire(import.meta.url)
const identifier = "b6372fe1-e9ee-4d12-b09c-929922f1d7e3"
const compiled = { expression: "close", expression_version: "a".repeat(64), formula_version: "stock-expression-v1", fields: ["close"], lookback: 0 }
const record = { ...compiled, id: identifier, revision: 1, latest_revision: 1, name: "已保存动量", direction: "high", description: "", disabled: false, created_at: "2026-10-23T02:00:00Z" }
const plain = (value) => JSON.parse(JSON.stringify(value))
const settle = () => new Promise((resolve) => setImmediate(resolve))

function mount({ enabled = true } = {}, store = new Map()) {
  const slots = [], effects = [], calls = []
  let cursor = 0, key = 0
  const props = { token: "token-one", scope: "owner:virtual" }
  const controls = { saveFails: false, deferred: null, listFails: false }
  const localStorage = { getItem: (name) => store.get(name), setItem: (name, value) => store.set(name, value), removeItem: (name) => store.delete(name) }
  const dependencies = {
    react: {
      useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial; return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value }] },
      useRef(initial) { const index = cursor++; if (!(index in slots)) slots[index] = { current: initial }; return slots[index] },
      useEffect(callback, deps) { const index = cursor++; if (!(index in slots) || deps.some((item, position) => item !== slots[index].deps[position])) { slots[index]?.cleanup?.(); slots[index] = { deps }; effects.push(() => { slots[index].cleanup = callback() }) } },
    },
    "@/lib/stock-custom-factor-api": {
      getCustomFactorAvailability: async () => ({ definitions_enabled: enabled, research_enabled: false, reason: "固定数据IC与回测尚未接通", fields: [{ id: "close", unit: "CNY/share", availability: "信号日及之前" }] }),
      listCustomFactors: async (query, token, signal, factorId) => { if (controls.listFails) throw new Error("列表暂不可用"); return { items: factorId ? [record] : [], total: factorId ? 1 : 0, has_more: false } },
      validateCustomFactor: async (expression, token) => { calls.push({ kind: "validate", expression, token }); return compiled },
      saveCustomFactor: async (body, token, signal, editing) => { calls.push({ kind: "save", body, token, signal, editing }); if (controls.saveFails) throw new Error("网络中断，请原键核对"); return controls.deferred ?? { ...record, name: body.name, expression: body.expression, revision: editing ? editing.expected_revision + 1 : 1, latest_revision: editing ? editing.expected_revision + 1 : 1 } },
      disableCustomFactor: async (id, body, token) => { calls.push({ kind: "disable", id, body, token }); return { ...record, disabled: true } },
    },
    "@/lib/stock-pending-writes": loadPendingWrites(localStorage, { crypto }),
  }
  const target = new URL("./stock-factor-editor.tsx", import.meta.url)
  const source = existsSync(target) ? readFileSync(target, "utf8") : "export const StockFactorEditor = undefined"
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const module = { exports: {} }
  vm.runInNewContext(output, { module, exports: module.exports, AbortController, Error, crypto: { randomUUID: () => `f682700b-7415-47d6-84c0-${String(++key).padStart(12, "0")}` }, localStorage, require: (name) => dependencies[name] ?? requireModule(name) })
  assert.equal(typeof module.exports.StockFactorEditor, "function", "real custom factor editor exists")
  const render = () => { cursor = 0; const tree = module.exports.StockFactorEditor(props); effects.splice(0).forEach((effect) => effect()); return tree }
  const ready = async () => { for (let index = 0; index < 4; index++) { render(); await settle() } }
  render()
  return { render, ready, calls, controls, props, storage: store, pendingWrites: dependencies["@/lib/stock-pending-writes"], unmount: () => slots.forEach((slot) => slot?.cleanup?.()) }
}

function nodes(tree) { if (Array.isArray(tree)) return tree.flatMap(nodes); return tree && typeof tree === "object" ? [tree, ...nodes(tree.props?.children)] : [] }
function text(tree) { if (Array.isArray(tree)) return tree.map(text).join(""); return tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }
function field(page, label) { const found = nodes(page.render()).find((node) => node.props?.["aria-label"] === label); assert.ok(found, label); return found }
function button(page, label) { const found = nodes(page.render()).find((node) => node.type === "button" && text(node) === label); assert.ok(found, label); return found }
function edit(page, label, value) { field(page, label).props.onChange({ target: { value } }) }

test("unmigrated capability hides definition writes, never pretends custom research is available", async () => {
  const page = mount({ enabled: false }); await page.ready()
  assert.match(text(page.render()), /尚未接通/)
  assert.ok(!nodes(page.render()).some((node) => node.type === "textarea"))
  assert.equal(page.calls.length, 0)
  page.unmount()
})

test("validation never saves, explicit save persists immutable definition then history is inspectable", async () => {
  const page = mount(); await page.ready()
  assert.match(text(page.render()), /不代表.*回测|尚未接通/)
  edit(page, "自定义因子名称", "新动量")
  edit(page, "自定义因子表达式", "close")
  assert.equal(button(page, "保存定义").props.disabled, true)
  button(page, "校验表达式").props.onClick(); await page.ready()
  assert.equal(page.calls.filter((call) => call.kind === "save").length, 0)
  assert.equal(button(page, "保存定义").props.disabled, false)
  button(page, "保存定义").props.onClick(); await page.ready()
  const saves = page.calls.filter((call) => call.kind === "save")
  assert.equal(saves.length, 1)
  assert.equal(saves[0].body.name, "新动量")
  assert.equal(saves[0].body.expression, "close")
  assert.match(text(page.render()), /v1/)
  button(page, "查看当前因子版本").props.onClick(); await page.ready()
  assert.match(text(page.render()), /已保存动量/)
  button(page, "停用当前因子").props.onClick(); await page.ready()
  assert.equal(page.calls.filter((call) => call.kind === "disable").length, 0)
  button(page, "确认停用").props.onClick(); await page.ready()
  assert.equal(page.calls.filter((call) => call.kind === "disable").length, 1)
  page.unmount()
})

test("ambiguous save keeps original command and idempotency key until explicit retry", async () => {
  const page = mount(); await page.ready()
  edit(page, "自定义因子名称", "动量"); edit(page, "自定义因子表达式", "close")
  button(page, "校验表达式").props.onClick(); await page.ready()
  page.controls.saveFails = true
  const save = button(page, "保存定义")
  save.props.onClick(); save.props.onClick(); await page.ready()
  assert.equal(page.calls.filter((call) => call.kind === "save").length, 1)
  assert.equal(field(page, "自定义因子名称").props.disabled, true)
  page.controls.saveFails = false
  button(page, "重试原请求").props.onClick(); await page.ready()
  const saves = page.calls.filter((call) => call.kind === "save")
  assert.equal(saves.length, 2)
  assert.equal(saves[0].body.idempotency_key, saves[1].body.idempotency_key)
  assert.deepEqual(saves[0].body, saves[1].body)
  page.unmount()
})

test("an unconfirmed custom factor write survives navigation and retries with the original idempotency key", async () => {
  const store = new Map()
  const page = mount({}, store)
  await page.ready()
  edit(page, "自定义因子名称", "跨页动量")
  edit(page, "自定义因子表达式", "close")
  edit(page, "自定义因子描述", "离开页面也不能丢")
  button(page, "校验表达式").props.onClick(); await page.ready()
  page.controls.saveFails = true
  button(page, "保存定义").props.onClick(); await page.ready()
  const original = page.calls.filter((call) => call.kind === "save")[0].body
  const recorded = plain(page.pendingWrites.readPendingWrites("owner:virtual"))
  assert.equal(recorded.length, 1)
  assert.equal(recorded[0].kind, "custom-factor-save")
  assert.equal(recorded[0].idempotency_key, original.idempotency_key)
  assert.match(text(page.render()), /未确认/)
  assert.doesNotMatch(text(page.render()), /切换会话会清理本地草稿/)
  page.unmount()
  assert.equal(plain(page.pendingWrites.readPendingWrites("owner:virtual")).length, 1, "卸载不得丢弃未确认写入")
  page.controls.saveFails = false
  const returned = mount({}, store)
  await returned.ready()
  assert.match(text(returned.render()), /重试原请求/)
  assert.equal(button(returned, "重试原请求").props.disabled, false)
  assert.equal(returned.calls.filter((call) => call.kind === "save").length, 0, "回填后不得自动重发写入")
  button(returned, "重试原请求").props.onClick(); await returned.ready()
  const retried = returned.calls.filter((call) => call.kind === "save")
  assert.equal(retried.length, 1)
  assert.equal(retried[0].body.idempotency_key, original.idempotency_key)
  assert.deepEqual(plain(retried[0].body), plain(original))
  assert.deepEqual(plain(returned.pendingWrites.readPendingWrites("owner:virtual")), [])
  assert.match(text(returned.render()), /定义已保存/)
})

test("an unconfirmed disable write is rehydrated for another user and mode only after it is confirmed", async () => {
  const store = new Map()
  const page = mount({}, store)
  await page.ready()
  edit(page, "自定义因子名称", "停用跨页"); edit(page, "自定义因子表达式", "close")
  button(page, "校验表达式").props.onClick(); await page.ready()
  button(page, "保存定义").props.onClick(); await page.ready()
  button(page, "停用当前因子").props.onClick(); await page.ready()
  button(page, "确认停用").props.onClick(); await page.ready()
  const written = page.calls.filter((call) => call.kind === "disable")[0]
  assert.equal(plain(page.pendingWrites.readPendingWrites("owner:virtual")).length, 0)
  page.unmount()
  const other = mount({}, store)
  other.props.token = "token-two"; other.props.scope = "other:live"
  await other.ready()
  assert.doesNotMatch(text(other.render()), /重试原请求/)
  const returned = mount({}, store)
  await returned.ready()
  assert.doesNotMatch(text(returned.render()), /重试原请求/)
  assert.equal(returned.calls.filter((call) => call.kind === "disable").length, 0)
  assert.equal(written.body.idempotency_key.length, 36)
})

test("switching user or mode hides old state immediately and ignores late save responses", async () => {
  const page = mount(); await page.ready()
  edit(page, "自定义因子名称", "旧用户私有"); edit(page, "自定义因子表达式", "close")
  button(page, "校验表达式").props.onClick(); await page.ready()
  let complete
  page.controls.deferred = new Promise((resolve) => { complete = resolve })
  button(page, "保存定义").props.onClick(); await settle()
  page.props.token = "token-two"; page.props.scope = "other:live"
  assert.ok(!text(page.render()).includes("旧用户私有"))
  await page.ready()
  complete({ ...record, name: "旧用户私有" }); await page.ready()
  assert.ok(!text(page.render()).includes("旧用户私有"))
  assert.equal(field(page, "自定义因子名称").props.value, "")
  assert.equal(page.calls.filter((call) => call.kind === "save").length, 1)
  page.unmount()
})

test("editing a saved definition explicitly creates the next revision and invalidates old validation", async () => {
  const page = mount(); await page.ready()
  edit(page, "自定义因子名称", "首版"); edit(page, "自定义因子表达式", "close")
  button(page, "校验表达式").props.onClick(); await page.ready()
  button(page, "保存定义").props.onClick(); await page.ready()
  edit(page, "自定义因子表达式", "close / lag(close, 20) - 1")
  assert.equal(button(page, "保存定义").props.disabled, true)
  edit(page, "自定义因子方向", "low")
  edit(page, "自定义因子描述", "历史版本不能改写")
  button(page, "校验表达式").props.onClick(); await page.ready()
  button(page, "保存定义").props.onClick(); await page.ready()
  const writes = page.calls.filter((call) => call.kind === "save")
  assert.equal(writes.length, 2)
  assert.equal(writes[1].editing.id, identifier)
  assert.equal(writes[1].editing.expected_revision, 1)
  assert.notEqual(writes[0].body.idempotency_key, writes[1].body.idempotency_key)
  assert.equal(writes[1].body.direction, "low")
  assert.equal(writes[1].body.description, "历史版本不能改写")
  assert.match(text(page.render()), /v2/)
  page.unmount()
})
