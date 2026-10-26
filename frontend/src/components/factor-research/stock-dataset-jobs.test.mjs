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
const detail = { id, kind: "dataset", status: "pending", parameters, request_id: "a".repeat(32), result: null, error_code: null }
const settle = () => new Promise((resolve) => setImmediate(resolve))
const pendingKey = "stock-pending-writes:owner:virtual"

function mount({ accepting = false, fails = false, submitFails = false, failFirstSubmit = false, command, storage, items } = {}) {
  const slots = [], effects = [], calls = []
  let cursor = 0
  const props = { token: "token-one", scope: "owner:virtual", command }
  const controls = { deferred: null, fails, submitFails, failFirstSubmit, submits: 0, deferredSubmit: null }
  const listed = items ?? [detail]
  const dependencies = {
    react: {
      useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial; return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value }] },
      useRef(initial) { const index = cursor++; if (!(index in slots)) slots[index] = { current: initial }; return slots[index] },
      useEffect(callback, deps) { const index = cursor++; if (!(index in slots) || deps.some((value, position) => value !== slots[index].deps[position])) { slots[index]?.cleanup?.(); slots[index] = { deps }; effects.push(() => { slots[index].cleanup = callback() }) } },
    },
    "@/lib/stock-dataset-jobs-api": {
      getStockDatasetJobAvailability: async (token) => { calls.push({ kind: "availability", token }); if (controls.fails) throw new Error("任务源未连接"); return { accepting, research_enabled: false, reason: "仅观察，计算未开放" } },
      listStockDatasetJobs: async (query, token) => { calls.push({ kind: "list", token }); return { items: listed, total: listed.length, has_more: false } },
      getStockDatasetJob: async (identifier, token, signal) => { calls.push({ kind: "detail", token, signal }); return controls.deferred ?? detail },
      cancelStockDatasetJob: async (identifier, token) => { calls.push({ kind: "cancel", token }); return { ...detail, status: "cancelled" } },
      submitStockDatasetJob: async (value, token) => { calls.push({ kind: "submit", value, token }); controls.submits += 1
        if (controls.submitFails && !(controls.failFirstSubmit && controls.submits > 1)) throw new Error("提交结果未知"); return controls.deferredSubmit ?? detail },
      // The real one narrows on the persisted kind *and* the payload shape; the mock keeps
      // that contract so a research-shaped payload cannot be read as an observation build.
      isObservationDetail: (job) => job?.kind === "dataset" && !!job.parameters && Array.isArray(job.parameters.source_ids),
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

// Migration 021 puts research kinds in the same task table, so the shared panel sees them
// even though it only ever submits observation builds.  It must name the kind it got and
// must not invent observation fields for a payload that has none.
const researchId = "5df7727e-dc73-428c-a526-83de24a1fcde"
test("a research job in the shared table is labelled by kind and never rendered as observation parameters", async () => {
  const research = { ...detail, id: researchId, kind: "research_ic", parameters: { kind: "research_ic", dataset: { dataset_id: id, data_version: "b".repeat(64), qualification: "research_only" }, factors: { factor_ids: ["ret_20d"], custom_factors: [], action: "analysis", industry_neutral: false, mktcap_neutral: false, weight: "equal" }, horizon: 20, groups: 5, decay_lags: [1, 5, 10, 20] } }
  const view = mount({ items: [detail, research] })
  await view.ready()
  assert.match(text(view.render()), /观察归档/)
  assert.match(text(view.render()), /因子IC/)
  view.controls.deferred = research
  nodes(view.render()).filter((node) => node.type === "button" && text(node) === "查看固定参数").at(-1).props.onClick()
  await view.ready()
  const shown = text(view.render())
  assert.match(shown, /因子IC/)
  assert.match(shown, /研究类型任务/)
  // The observation-only fields belong to the observation build; a research payload has none.
  assert.doesNotMatch(shown, /固定来源：/)
  assert.doesNotMatch(shown, /本人固定池/)
  assert.match(shown, new RegExp(researchId))
  view.unmount()
})

test("identity switch hides prior details immediately and drops delayed old-session replies", async () => {  const view = mount()
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
  // 全市场提交的签名会超出单字段 8192 上限，登记被拒（invalid_entry）；此时不得承诺可重试
  const command = { idempotency_key: id, parameters: { ...parameters, instruments: Array.from({ length: 900 }, (_, index) => `6${String(index).padStart(5, "0")}.SH`) } }
  const signature = JSON.stringify(command)
  assert.ok(signature.length > 8192)
  const storage = memoryStorage()
  const view = mount({ accepting: true, command, storage, submitFails: true })
  await view.ready()
  button(view.render(), "确认创建观察归档任务").props.onClick()
  await view.ready()
  assert.equal(storage.getItem(pendingKey), null)
  assert.equal(nodes(view.render()).some((node) => node.type === "button" && text(node) === "用原幂等键重试"), false)
  assert.match(text(view.render()), /请求恢复信息无效|尚未发送/)
  view.unmount()
})

test("a key already held by a different command is refused, never retried under that key", async () => {
  // 注册表对同键不同内容返回 key_conflict 并**保留旧条目**。此时读回仍能查到该键，
  // 只靠读回核实就会把这条新命令当成可重试的旧键——服务端按 (owner, mode, key) 去重，
  // 会返回旧任务，而界面显示的是新参数。必须按回执判定，并明确拒绝。
  const conflict = "3f0c5b8a-1d4e-4c27-9a63-7b1e5d2f8c04"
  const storage = memoryStorage({
    [pendingKey]: JSON.stringify([{ kind: "dataset-job", idempotency_key: conflict,
      signature: JSON.stringify({ idempotency_key: conflict }), created_at: "2026-10-26T00:00:00.000Z" }]),
  })
  const view = mount({ accepting: true, command: { idempotency_key: conflict, parameters }, storage, submitFails: true })
  await view.ready()
  assert.equal(view.calls.filter((call) => call.kind === "submit").length, 0)
  button(view.render(), "确认创建观察归档任务").props.onClick()
  await view.ready()
  // 登记被拒绝：不发出请求，也不把这条从未登记成功的新命令包装成可重试的旧键
  assert.equal(view.calls.filter((call) => call.kind === "submit").length, 0)
  assert.equal(nodes(view.render()).some((node) => node.type === "button" && text(node) === "用原幂等键重试"), false)
  assert.match(text(view.render()), /不同内容|冲突|已占用/)
  // 旧条目必须原样保留：不能为了腾位置或掩盖冲突而改写别人的未确认请求
  assert.deepEqual(JSON.parse(storage.getItem(pendingKey)).map((entry) => entry.idempotency_key), [conflict])
  view.unmount()
})
