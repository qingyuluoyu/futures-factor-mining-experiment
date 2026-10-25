import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"

const requireModule = createRequire(import.meta.url)
const id = "74490e6b-b979-4331-87d8-c25b809075dd"
const parameters = { source_ids: [id], pool_id: "本人固定池", pool_version: "a".repeat(64), instruments: ["000001.SZ"], start: "2026-01-05", end: "2026-01-06", warmup_start: "2026-01-05" }
const detail = { id, status: "pending", parameters, request_id: "a".repeat(32), result: null, error_code: null }
const settle = () => new Promise((resolve) => setImmediate(resolve))

function mount({ accepting = false, fails = false, command } = {}) {
  const slots = [], effects = [], calls = []
  let cursor = 0
  const props = { token: "token-one", scope: "owner:virtual", command }
  const controls = { deferred: null, fails }
  const dependencies = {
    react: {
      useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial; return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value }] },
      useRef(initial) { const index = cursor++; if (!(index in slots)) slots[index] = { current: initial }; return slots[index] },
      useEffect(callback, deps) { const index = cursor++; if (!(index in slots) || deps.some((value, position) => value !== slots[index].deps[position])) { slots[index]?.cleanup?.(); slots[index] = { deps }; effects.push(() => { slots[index].cleanup = callback() }) } },
    },
    "@/lib/stock-dataset-jobs-api": {
      getStockDatasetJobAvailability: async (token) => { calls.push({ kind: "availability", token }); if (controls.fails) throw new Error("任务源未连接"); return { accepting, research_enabled: false, reason: "仅观察，计算未开放" } },
      listStockDatasetJobs: async (query, token) => { calls.push({ kind: "list", token }); return { items: [detail], total: 1, has_more: false } },
      getStockDatasetJob: async (identifier, token, signal) => { calls.push({ kind: "detail", token, signal }); return controls.deferred ?? detail },
      cancelStockDatasetJob: async (identifier, token) => { calls.push({ kind: "cancel", token }); return { ...detail, status: "cancelled" } },
      submitStockDatasetJob: async (value, token) => { calls.push({ kind: "submit", value, token }); return detail },
    },
  }
  const target = new URL("./stock-dataset-jobs.tsx", import.meta.url)
  const source = existsSync(target) ? readFileSync(target, "utf8") : "export const StockDatasetJobs = undefined"
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const module = { exports: {} }
  vm.runInNewContext(output, { module, exports: module.exports, AbortController, Error, require: (name) => dependencies[name] ?? requireModule(name) })
  assert.equal(typeof module.exports.StockDatasetJobs, "function")
  const render = () => { cursor = 0; const tree = module.exports.StockDatasetJobs(props); effects.splice(0).forEach((effect) => effect()); return tree }
  const ready = async () => { for (let index = 0; index < 4; index++) { render(); await settle() } }
  render()
  return { render, ready, calls, controls, props, unmount: () => slots.forEach((slot) => slot?.cleanup?.()) }
}
function nodes(tree) { if (Array.isArray(tree)) return tree.flatMap(nodes); return tree && typeof tree === "object" ? [tree, ...nodes(tree.props?.children)] : [] }
function text(tree) { if (Array.isArray(tree)) return tree.map(text).join(""); return tree && typeof tree === "object" ? text(tree.props?.children) : String(tree ?? "") }
function button(tree, label) { const found = nodes(tree).find((node) => node.type === "button" && text(node) === label); assert.ok(found, label); return found }

test("task panel preserves unavailable state and does not create tasks on mount", async () => {
  const view = mount({ command: { idempotency_key: id, parameters } })
  await view.ready()
  assert.match(text(view.render()), /仅观察|观察/)
  assert.equal(button(view.render(), "确认创建观察归档任务").props.disabled, true)
  assert.equal(view.calls.filter((call) => call.kind === "submit").length, 0)
  view.unmount()
})

test("explicit confirmation submits the same key and cancellation works while accepting is false", async () => {
  const command = { idempotency_key: id, parameters }
  const view = mount({ accepting: true, command })
  await view.ready()
  button(view.render(), "确认创建观察归档任务").props.onClick()
  await view.ready()
  assert.equal(view.calls.find((call) => call.kind === "submit").value.idempotency_key, id)
  view.unmount()
  const readonly = mount()
  await readonly.ready()
  button(readonly.render(), "查看固定参数").props.onClick()
  await readonly.ready()
  assert.equal(button(readonly.render(), "取消所选任务").props.disabled, false)
  button(readonly.render(), "取消所选任务").props.onClick()
  await readonly.ready()
  assert.match(text(readonly.render()), /已取消/)
  assert.equal(readonly.calls.filter((call) => call.kind === "cancel").length, 1)
  readonly.unmount()
})

test("identity switch hides prior details immediately and drops delayed old-session replies", async () => {
  const view = mount()
  await view.ready()
  let resolve
  view.controls.deferred = new Promise((done) => { resolve = done })
  button(view.render(), "查看固定参数").props.onClick()
  view.props.token = "token-two"
  view.props.scope = "other:live"
  assert.doesNotMatch(text(view.render()), /本人固定池/)
  resolve(detail)
  await view.ready()
  assert.doesNotMatch(text(view.render()), /本人固定池/)
  assert.equal(view.calls.filter((call) => call.kind === "cancel").length, 0)
  view.unmount()
})

test("request failure is visible and does not become a successful zero-count result", async () => {
  const view = mount({ fails: true })
  await view.ready()
  assert.match(text(view.render()), /任务源未连接/)
  assert.doesNotMatch(text(view.render()), /任务共0条/)
  view.unmount()
})
