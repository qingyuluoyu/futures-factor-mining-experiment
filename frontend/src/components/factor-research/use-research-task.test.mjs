import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import vm from "node:vm"
import { createRequire } from "node:module"
import ts from "typescript"
import * as realApi from "../../lib/stock-research-api.ts"

const id = "11111111-1111-4111-8111-111111111111"
const pending = { id, kind: "analysis", title: "检验", status: "pending", step: "queued", progress: 0, request_id: "trace", attempts: 0, created_at: "2026-10-18", updated_at: "2026-10-18", started_at: null, finished_at: null, method_version: "v1", workflow_version: "v1", data_version: null, has_result: false, error: null }
const completed = { ...pending, status: "completed", progress: 100, has_result: true, data_version: "hash", finished_at: "2026-10-18" }

function mount({ createFails = false, restored = null } = {}) {
  const source = readFileSync(new URL("./use-research-task.ts", import.meta.url), "utf8")
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
  const slots = [], effects = [], cleanups = [], timers = new Map(), storage = new Map()
  const calls = { creates: [], gets: 0, cancels: 0, results: 0, availability: 0 }
  const controls = { createFails, createStatus: undefined, cancelStatus: undefined, nextJob: pending, getFails: false, deferredJob: null, deferredCancel: null, availabilityReplies: [], availabilityFails: false, accepting: true, now: Date.now() }
  let cursor = 0, mounted = false, timerId = 0, authToken = "token", sessionRevision = 1, marketType = "stock"
  if (restored) storage.set("stock-research:user:virtual", JSON.stringify(restored))
  const dependencies = {
    react: {
      useState(initial) { const position = cursor++; if (!(position in slots)) slots[position] = typeof initial === "function" ? initial() : initial; return [slots[position], (value) => { slots[position] = typeof value === "function" ? value(slots[position]) : value }] },
      useRef(initial) { const position = cursor++; if (!(position in slots)) slots[position] = { current: initial }; return slots[position] },
      useCallback: (callback) => callback,
      useEffect: (effect) => { if (!mounted) effects.push(effect) },
    },
    "@/stores/auth": { useAuthStore: Object.assign((selector) => selector({ sessionRevision, accessToken: authToken, user: { id: "user", market_type: marketType } }), { getState: () => ({ sessionRevision, accessToken: authToken, user: { id: "user", market_type: marketType } }) }) },
    "@/lib/stock-research-api": {
      ...realApi, stockResearchScope: () => "user:virtual",
      getStockResearchAvailability: async () => { calls.availability++; if (controls.availabilityFails) throw new Error("availability unavailable"); return controls.availabilityReplies.length ? controls.availabilityReplies.shift() : { enabled: true, accepting: controls.accepting } },
      createStockResearchJob: async (body) => { calls.creates.push(structuredClone(body)); if (controls.createFails) throw Object.assign(new Error("connection lost"), { status: controls.createStatus }); return pending },
      getStockResearchJob: async () => { calls.gets++; if (controls.getFails) throw Object.assign(new Error("poll failed"), { status: controls.getStatus }); return controls.deferredJob ?? controls.nextJob },
      cancelStockResearchJob: async () => { calls.cancels++; if (controls.cancelStatus) throw Object.assign(new Error("任务已结束，不能取消"), { status: controls.cancelStatus }); return controls.deferredCancel ?? { ...pending, status: "cancelled" } },
      getStockResearchResult: async () => { calls.results++; return { research_job_id: id, data_version: "hash" } },
    },
  }
  const module = { exports: {} }, requireModule = createRequire(import.meta.url)
  vm.runInNewContext(output, { module, exports: module.exports, require: (name) => dependencies[name] ?? requireModule(name), AbortController, Error, crypto: globalThis.crypto, localStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) }, setTimeout: (callback) => { const key = ++timerId; timers.set(key, callback); return key }, clearTimeout: (key) => timers.delete(key), Date: class extends Date { static now() { return controls.now } }, JSON })
  function render() { cursor = 0; const hook = module.exports.useResearchTask("token", "user:virtual"); mounted = true; return hook }
  render()
  return { render, calls, controls, timers, storage, async ready() { effects.forEach((effect) => cleanups.push(effect())); await settle() }, async tick() { const callbacks = [...timers.values()]; timers.clear(); for (const callback of callbacks) callback(); await settle() }, unmount() { cleanups.forEach((cleanup) => cleanup?.()) }, async replayEffect() { cleanups.splice(0).forEach((cleanup) => cleanup?.()); effects.forEach((effect) => cleanups.push(effect())); await settle() }, switchSession() { authToken = "new-session" }, sameTokenNewSession() { sessionRevision++ }, switchMarket() { marketType = "futures" } }
}
const settle = () => new Promise((resolve) => setImmediate(resolve))
const parameters = { factor_id: "ret_20d", start_date: "2026-07-13", end_date: "2026-09-11" }

test("history draft application stays gated through deferred owner task recovery", async () => {
  let finish
  const page = mount({ restored: { jobId: id } })
  assert.equal(page.render().recoveryPending, true)
  page.controls.deferredJob = new Promise((resolve) => { finish = resolve })
  await page.ready()
  assert.equal(page.render().recoveryPending, true)
  await page.render().submit("analysis", parameters)
  assert.equal(page.calls.creates.length, 0)
  finish(pending); await settle()
  assert.equal(page.render().recoveryPending, false)
  assert.equal(page.render().blocked, true)
})

test("no-recovery initialization opens the draft gate only after availability, and failures retry safely", async () => {
  const page = mount()
  assert.equal(page.render().recoveryPending, true)
  page.controls.availabilityFails = true
  await page.ready()
  assert.equal(page.render().recoveryPending, true)
  await page.render().submit("analysis", parameters)
  assert.equal(page.calls.creates.length, 0)
  page.controls.availabilityFails = false
  await page.render().retry()
  assert.equal(page.render().recoveryPending, false)
  assert.equal(page.render().blocked, false)
})

test("failed recovery and original unknown submission keep draft replacement blocked", async () => {
  const saved = mount({ restored: { jobId: id } }); saved.controls.getFails = true; await saved.ready()
  assert.equal(saved.render().recoveryPending, true)
  saved.controls.getFails = false; await saved.render().retry()
  assert.equal(saved.render().recoveryPending, false)
  const original = realApi.researchCreateSchema.parse({ idempotency_key: id, kind: "analysis", parameters })
  const uncertain = mount({ restored: { draft: original } }); await uncertain.ready()
  assert.equal(uncertain.render().recoveryPending, true)
  assert.equal(uncertain.render().blocked, true)
  await uncertain.render().submit("analysis", parameters)
  assert.equal(uncertain.calls.creates.length, 0)
  await uncertain.render().retry()
  assert.equal(uncertain.calls.creates[0].idempotency_key, id)
  assert.equal(uncertain.render().recoveryPending, false)
})

test("owner-checked 404 clears the saved recovery gate without creating work", async () => {
  const page = mount({ restored: { jobId: id } })
  page.controls.getFails = true; page.controls.getStatus = 404
  await page.ready()
  assert.equal(page.render().recoveryPending, false)
  assert.equal(page.render().blocked, false)
  assert.equal(page.render().job, null)
  assert.deepEqual(JSON.parse(page.storage.get("stock-research:user:virtual")), {})
  assert.equal(page.calls.creates.length, 0)
})

test("disabled service cannot resolve a saved recovery and invalid recovery records stay blocked", async () => {
  const disabled = mount({ restored: { jobId: id } })
  disabled.controls.availabilityReplies.push({ enabled: false, accepting: false })
  await disabled.ready()
  assert.equal(disabled.render().recoveryPending, true)
  assert.equal(disabled.calls.gets, 0)
  const invalid = mount()
  invalid.storage.set("stock-research:user:virtual", "invalid-json")
  await invalid.ready()
  assert.equal(invalid.render().recoveryPending, true)
  assert.equal(invalid.render().blocked, true)
  await invalid.render().submit("analysis", parameters)
  assert.equal(invalid.calls.creates.length, 0)
})

test("network creation failure retains the same idempotency key across explicit retry", async () => {
  const page = mount({ createFails: true }); await page.ready()
  await page.render().submit("analysis", parameters)
  assert.match(page.render().error, /connection lost/)
  assert.equal(page.timers.size, 0)
  assert.equal(page.render().blocked, true)
  page.controls.createFails = false
  await page.render().retry()
  assert.equal(page.calls.creates.length, 2)
  assert.equal(page.calls.creates[0].idempotency_key, page.calls.creates[1].idempotency_key)
  assert.equal(page.render().job.id, id)
})

test("active task refresh recovery polls and stops permanently on completion", async () => {
  const page = mount({ restored: { jobId: id } }); await page.ready()
  assert.equal(page.render().job.id, id)
  assert.equal(page.timers.size, 1)
  page.controls.nextJob = completed
  await page.tick()
  assert.equal(page.render().job.status, "completed")
  assert.equal(page.calls.results, 1)
  assert.equal(page.timers.size, 0)
})

test("poll failure is visible and stops until manual retry; cancellation retains report", async () => {
  const page = mount({ restored: { jobId: id } }); await page.ready()
  page.controls.getFails = true
  await page.tick()
  assert.match(page.render().error, /poll failed/)
  assert.equal(page.timers.size, 0)
  page.controls.getFails = false
  await page.render().retry()
  await page.render().cancel()
  assert.equal(page.calls.cancels, 1)
  assert.equal(page.render().job.status, "cancelled")
  assert.equal(page.timers.size, 0)
  assert.match([...page.storage.values()][0], new RegExp(id))
})

test("session change ignores late results and does not schedule another poll", async () => {
  const page = mount({ restored: { jobId: id } }); await page.ready()
  page.switchSession(); page.controls.nextJob = completed
  await page.tick()
  assert.equal(page.calls.results, 0)
  assert.equal(page.timers.size, 0)
  assert.equal(page.render().result, null)
})

test("same-token new session and market switches reject in-flight recovered work and stale submit", async () => {
  for (const operation of ["sameTokenNewSession", "switchMarket"]) {
    const page = mount({ restored: { jobId: id } })
    let finish
    page.controls.deferredJob = new Promise((resolve) => { finish = resolve })
    await page.ready()
    page[operation]()
    finish(completed); await settle()
    assert.equal(page.render().result, null)
    assert.equal(page.calls.results, 0)
    assert.equal(page.timers.size, 0)
    assert.equal(page.render().job, null)
    const ready = mount(); await ready.ready()
    const staleSubmit = ready.render().submit
    ready[operation]()
    await staleSubmit("analysis", parameters)
    assert.equal(ready.calls.creates.length, 0)
  }
})

test("unmount clears scheduled polls and ignores an in-flight completion", async () => {
  const page = mount({ restored: { jobId: id } }); await page.ready()
  let finish
  page.controls.deferredJob = new Promise((resolve) => { finish = resolve })
  await page.tick()
  page.unmount()
  finish(completed)
  await settle()
  assert.equal(page.calls.results, 0)
  assert.equal(page.timers.size, 0)
  assert.equal(page.render().result, null)
})

test("bounded polling stops pending work and requires explicit manual continuation", async () => {
  const page = mount({ restored: { jobId: id } }); await page.ready()
  for (let attempt = 0; attempt < 541; attempt++) await page.tick()
  assert.equal(page.timers.size, 0)
  assert.match(page.render().error, /上限/)
  assert.equal(page.render().blocked, true)
  assert.equal(page.calls.results, 0)
})

test("wall time bound also stops polling when network requests are slow", async () => {
  const page = mount({ restored: { jobId: id } }); await page.ready()
  page.controls.now += 18 * 60 * 1000
  await page.tick()
  assert.equal(page.timers.size, 0)
  assert.match(page.render().error, /上限/)
  assert.equal(page.render().blocked, true)
})

test("restored unknown submission can replay its original blank-end draft across the Shanghai midnight", async () => {
  const RealDate = Date
  let now = RealDate.parse("2026-10-18T15:59:00Z")
  globalThis.Date = class extends RealDate { constructor(...args) { super(...(args.length ? args : [now])) } static now() { return now } }
  try {
    const start_date = new RealDate(RealDate.parse("2026-10-18T00:00:00Z") - 3660 * 86400000).toISOString().slice(0, 10)
    const original = realApi.researchCreateSchema.parse({ idempotency_key: id, kind: "analysis", parameters: { factor_id: "ret_20d", start_date, end_date: "" } })
    now = RealDate.parse("2026-10-18T16:01:00Z")
    const page = mount({ restored: { draft: original } }); await page.ready()
    assert.equal(page.render().blocked, true)
    assert.match(page.render().error, /原幂等键/)
    await page.render().retry()
    assert.equal(page.calls.creates.length, 1)
    assert.deepEqual(page.calls.creates[0], original)
    assert.equal(page.render().job.id, id)
  } finally { globalThis.Date = RealDate }
})

test("cancel queues behind an in-flight GET and repeated clicks send one cancellation POST", async () => {
  const page = mount({ restored: { jobId: id } }); await page.ready()
  let finishGet, finishCancel
  page.controls.deferredJob = new Promise((resolve) => { finishGet = resolve })
  page.controls.deferredCancel = new Promise((resolve) => { finishCancel = resolve })
  await page.tick()
  await page.render().cancel(); await page.render().cancel()
  assert.equal(page.render().cancelling, true)
  assert.equal(page.calls.cancels, 0)
  finishGet(pending); await settle()
  assert.equal(page.calls.cancels, 1)
  await page.render().cancel()
  assert.equal(page.calls.cancels, 1)
  finishCancel({ ...pending, status: "cancelled" }); await settle()
  assert.equal(page.render().job.status, "cancelled")
  assert.equal(page.render().cancelling, false)
  assert.equal(page.timers.size, 0)
})

test("queued cancellation does not POST after job completion or session change", async () => {
  for (const changedSession of [false, true]) {
    const page = mount({ restored: { jobId: id } }); await page.ready()
    let finishGet
    page.controls.deferredJob = new Promise((resolve) => { finishGet = resolve })
    await page.tick(); await page.render().cancel()
    if (changedSession) page.switchSession()
    finishGet(changedSession ? pending : completed); await settle()
    assert.equal(page.calls.cancels, 0)
    assert.equal(page.timers.size, 0)
  }
})

test("manual availability refresh recovers worker acceptance without creating work", async () => {
  const page = mount(); page.controls.accepting = false; await page.ready()
  assert.equal(page.render().availability.accepting, false)
  page.controls.accepting = true
  await page.render().refreshAvailability()
  assert.equal(page.render().availability.accepting, true)
  assert.equal(page.calls.availability, 2)
  assert.equal(page.calls.creates.length, 0)
})

test("confirmed creation 422 clears the rejected draft and permits corrected input with a new key", async () => {
  const page = mount({ createFails: true }); page.controls.createStatus = 422; await page.ready()
  await page.render().submit("analysis", parameters)
  assert.equal(page.render().blocked, false)
  assert.equal(JSON.parse([...page.storage.values()][0]).draft, undefined)
  assert.match(page.render().error, /connection lost/)
  page.controls.createFails = false
  await page.render().submit("analysis", { ...parameters, horizon: 10 })
  assert.equal(page.calls.creates.length, 2)
  assert.notEqual(page.calls.creates[0].idempotency_key, page.calls.creates[1].idempotency_key)
  assert.equal(page.calls.creates[1].parameters.horizon, 10)
})

test("network, 429 and 503 creation failures preserve draft and original retry key", async () => {
  for (const status of [undefined, 429, 503]) {
    const page = mount({ createFails: true }); page.controls.createStatus = status; await page.ready()
    await page.render().submit("analysis", parameters)
    assert.equal(page.render().blocked, true)
    await page.render().submit("analysis", { ...parameters, horizon: 10 })
    assert.equal(page.calls.creates.length, 1)
    page.controls.createFails = false
    await page.render().retry()
    assert.equal(page.calls.creates.length, 2)
    assert.deepEqual(page.calls.creates[0], page.calls.creates[1])
  }
})

test("StrictMode cleanup and effect restart ignore rejection from the original aborted request", async () => {
  const page = mount()
  let rejectOriginal
  page.controls.availabilityReplies.push(new Promise((_resolve, reject) => { rejectOriginal = reject }))
  await page.ready()
  await page.replayEffect()
  assert.equal(page.render().availability.accepting, true)
  rejectOriginal(new Error("signal is aborted without reason")); await settle()
  assert.equal(page.render().error, "")
  assert.equal(page.render().availability.accepting, true)
})

test("cancel 409 refreshes the real failed terminal state and unlocks input without another POST", async () => {
  const page = mount({ restored: { jobId: id } }); page.controls.nextJob = { ...pending, status: "running" }; await page.ready()
  page.controls.cancelStatus = 409
  page.controls.nextJob = { ...pending, status: "failed", error: { code: "missing_price", message: "持仓缺价", status: 409 } }
  await page.render().cancel()
  assert.equal(page.calls.cancels, 1)
  assert.equal(page.calls.gets, 2)
  assert.equal(page.calls.creates.length, 0)
  assert.equal(page.render().job.status, "failed")
  assert.equal(page.render().blocked, false)
  assert.equal(page.render().cancelling, false)
  assert.match(page.render().error, /持仓缺价/)
  assert.equal(page.timers.size, 0)
})

test("cancel 409 with failed state refresh preserves a visible error and permits only manual GET retry", async () => {
  const page = mount({ restored: { jobId: id } }); await page.ready()
  page.controls.cancelStatus = 409
  page.controls.getFails = true
  await page.render().cancel()
  assert.equal(page.calls.cancels, 1)
  assert.equal(page.calls.gets, 2)
  assert.match(page.render().error, /任务已结束/)
  assert.match(page.render().error, /poll failed/)
  assert.equal(page.render().blocked, true)
  assert.equal(page.timers.size, 0)
  page.controls.getFails = false
  page.controls.nextJob = completed
  await page.render().retry()
  assert.equal(page.calls.cancels, 1)
  assert.equal(page.calls.gets, 3)
  assert.equal(page.calls.creates.length, 0)
  assert.equal(page.render().job.status, "completed")
  assert.equal(page.render().blocked, false)
  assert.equal(page.calls.results, 1)
})
