import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"
import { loadPendingWrites, memoryStorage } from "../../lib/stock-pending-writes-harness.mjs"

const requireModule = createRequire(import.meta.url)
const id = "74490e6b-b979-4331-87d8-c25b809075dd"
const parameters = { source_ids: [id], pool_id: "本人固定池", pool_version: "a".repeat(64), instruments: ["000001.SZ"], start: "2026-01-05", end: "2026-01-06", warmup_start: "2026-01-05" }
const detail = { id, status: "pending", parameters, request_id: "a".repeat(32), result: null, error_code: null }
const settle = () => new Promise((resolve) => setImmediate(resolve))
const pendingKey = "stock-pending-writes:owner:virtual"

function mount({ accepting = false, fails = false, submitFails = false, failFirstSubmit = false, command, storage } = {}) {
  const slots = [], effects = [], calls = []
  let cursor = 0
  const props = { token: "token-one", scope: "owner:virtual", command }
  const controls = { deferred: null, fails, submitFails, failFirstSubmit, submits: 0, deferredSubmit: null }
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
      submitStockDatasetJob: async (value, token) => { calls.push({ kind: "submit", value, token }); controls.submits += 1
        if (controls.submitFails && !(controls.failFirstSubmit && controls.submits > 1)) throw new Error("提交结果未知"); return controls.deferredSubmit ?? detail },
    },
    "@/lib/stock-pending-writes": loadPendingWrites(storage ?? memoryStorage(), { crypto: globalThis.crypto }),
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

test("a confirmed submit registers then clears the unconfirmed write and never stores the token", async () => {
  const command = { idempotency_key: id, parameters }
  const storage = memoryStorage()
  const view = mount({ accepting: true, command, storage })
  await view.ready()
  let finish
  view.controls.deferredSubmit = new Promise((done) => { finish = done })
  button(view.render(), "确认创建观察归档任务").props.onClick()
  await settle()
  // 请求已在飞行中：此刻必须已按原键登记，且只存那五个字段
  const stored = JSON.parse(storage.getItem(pendingKey))
  assert.equal(stored.length, 1)
  assert.equal(stored[0].kind, "dataset-job")
  assert.equal(stored[0].idempotency_key, id)
  assert.equal(stored[0].signature, JSON.stringify(command))
  assert.deepEqual(Object.keys(stored[0]).sort(), ["created_at", "idempotency_key", "kind", "signature"])
  assert.doesNotMatch(JSON.stringify(stored), /token/)
  finish(detail)
  await view.ready()
  assert.equal(view.calls.find((call) => call.kind === "submit").value.idempotency_key, id)
  assert.equal(storage.getItem(pendingKey), null)
  assert.equal(nodes(view.render()).some((node) => node.type === "button" && text(node) === "用原幂等键重试"), false)
  view.unmount()
})

test("an unknown submit outcome keeps the original key and a returning page retries it verbatim", async () => {
  const command = { idempotency_key: id, parameters }
  const storage = memoryStorage()
  const view = mount({ accepting: true, command, storage, submitFails: true })
  await view.ready()
  button(view.render(), "确认创建观察归档任务").props.onClick()
  await view.ready()
  const stored = JSON.parse(storage.getItem(pendingKey))
  assert.equal(stored.length, 1)
  assert.equal(stored[0].kind, "dataset-job")
  assert.equal(stored[0].idempotency_key, id)
  assert.equal(stored[0].signature, JSON.stringify(command))
  assert.deepEqual(Object.keys(stored[0]).sort(), ["created_at", "idempotency_key", "kind", "signature"])
  assert.doesNotMatch(JSON.stringify(stored), /token/)
  view.unmount()

  // 换页回来（不带 command）不会自动重发，但可以用原键重试
  const returned = mount({ accepting: true, storage })
  await returned.ready()
  assert.equal(returned.calls.filter((call) => call.kind === "submit").length, 0)
  button(returned.render(), "用原幂等键重试").props.onClick()
  await returned.ready()
  const submits = returned.calls.filter((call) => call.kind === "submit")
  assert.equal(submits.length, 1)
  assert.equal(submits[0].value.idempotency_key, id)
  // 逐字节比较：跨 vm 领域取回的对象原型不同，deepStrictEqual 会误报
  assert.equal(JSON.stringify(submits[0].value), JSON.stringify(command))
  assert.equal(storage.getItem(pendingKey), null)
  returned.unmount()
})

test("a retry is still offered while the unknown-outcome error is on screen", async () => {
  const command = { idempotency_key: id, parameters }
  const storage = memoryStorage()
  const view = mount({ accepting: true, command, storage, submitFails: true, failFirstSubmit: true })
  await view.ready()
  button(view.render(), "确认创建观察归档任务").props.onClick()
  await view.ready()
  assert.match(text(view.render()), /提交结果未知/)
  // 错误提示仍在，但重试入口不能被它堵死
  assert.equal(button(view.render(), "用原幂等键重试").props.disabled, false)
  button(view.render(), "用原幂等键重试").props.onClick()
  await view.ready()
  const submits = view.calls.filter((call) => call.kind === "submit")
  assert.equal(submits.length, 2)
  assert.equal(submits[1].value.idempotency_key, id)
  assert.equal(storage.getItem(pendingKey), null)
  view.unmount()
})

test("another login scope or another write kind never surfaces as a retryable job", async () => {
  const storage = memoryStorage({
    "stock-pending-writes:someone-else:live": JSON.stringify([{ kind: "dataset-job", idempotency_key: id,
      signature: JSON.stringify({ idempotency_key: id, parameters }), created_at: "2026-10-26T00:00:00.000Z" }]),
    "stock-pending-writes:owner:virtual": JSON.stringify([{ kind: "report-bookmark", idempotency_key: "k1",
      signature: JSON.stringify({ id, bookmarked: true }), created_at: "2026-10-26T00:00:00.000Z" }]),
  })
  const view = mount({ accepting: true, storage })
  await view.ready()
  assert.equal(nodes(view.render()).some((node) => node.type === "button" && text(node) === "用原幂等键重试"), false)
  assert.equal(view.calls.filter((call) => call.kind === "submit").length, 0)
  view.unmount()
})

test("a command too large to register gets no retry promise", async () => {
  // 全市场提交会超出单字段 8192 上限，登记被静默丢弃；此时不得承诺可重试
  const command = { idempotency_key: id, parameters: { ...parameters, instruments: Array.from({ length: 900 }, (_, index) => `6${String(index).padStart(5, "0")}.SH`) } }
  assert.ok(JSON.stringify(command).length > 8192)
  const storage = memoryStorage()
  const view = mount({ accepting: true, command, storage, submitFails: true })
  await view.ready()
  button(view.render(), "确认创建观察归档任务").props.onClick()
  await view.ready()
  assert.equal(storage.getItem(pendingKey), null)
  assert.equal(nodes(view.render()).some((node) => node.type === "button" && text(node) === "用原幂等键重试"), false)
  assert.match(text(view.render()), /无法登记|不能跨页|过大/)
  view.unmount()
})
