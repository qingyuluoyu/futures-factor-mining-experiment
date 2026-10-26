import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"
import { loadPendingWrites, memoryStorage } from "../../lib/stock-pending-writes-harness.mjs"

const id = "11111111-1111-4111-8111-111111111111"
const empty = { job_id: id, market: "stock", bookmarked: false, created_at: null }
const settle = () => new Promise((resolve) => setImmediate(resolve))

function mount(options = {}) {
  const source = readFileSync(new URL("./research-report-bookmark.tsx", import.meta.url), "utf8")
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const slots = [], effects = [], calls = { read: [], write: [], changed: 0, callbacks: [] }
  let cursor = 0
  const controls = { jobId: id, callbackLabel: "initial", view: empty, readError: null, writeError: null, deferredRead: null, deferredWrite: null,
    auth: { user: { id: "user", market_type: "stock" }, accessToken: "token", sessionRevision: 1 }, storage: options.storage ?? memoryStorage() }
  const deps = {
    react: {
      useState(initial) { const slot = cursor++; if (!(slot in slots)) slots[slot] = initial; return [slots[slot], (value) => { slots[slot] = typeof value === "function" ? value(slots[slot]) : value }] },
      useRef(initial) { const slot = cursor++; if (!(slot in slots)) slots[slot] = { current: initial }; return slots[slot] },
      useEffect(callback, values) { const slot = cursor++; if (!(slot in slots) || values.some((value, index) => value !== slots[slot].values[index])) { slots[slot]?.cleanup?.(); slots[slot] = { values }; effects.push(() => { slots[slot].cleanup = callback() }) } },
    },
    "@/stores/auth": { useAuthStore: { getState: () => controls.auth } },
    "@/lib/stock-research-api": {
      stockResearchScope: () => "user:virtual",
      getStockReportBookmark: async (job, token, signal) => { calls.read.push({ job, token, signal }); if (controls.readError) throw controls.readError; return controls.deferredRead ?? controls.view },
      setStockReportBookmark: async (job, desired, token, signal) => { calls.write.push({ job, desired, token, signal }); if (controls.writeError) throw controls.writeError; return controls.deferredWrite ?? { ...empty, job_id: job, bookmarked: desired, created_at: desired ? "2026-10-22T00:00:00Z" : null } },
    },
    "@/lib/stock-pending-writes": loadPendingWrites(controls.storage, { crypto: globalThis.crypto }),
  }
  const module = { exports: {} }, nativeRequire = createRequire(import.meta.url)
  vm.runInNewContext(output, { module, exports: module.exports, require: (name) => deps[name] ?? nativeRequire(name), AbortController, Error })
  function render() { cursor = 0; const label = controls.callbackLabel; const tree = module.exports.ResearchReportBookmark({ jobId: controls.jobId, token: "token", scope: "user:virtual", onChanged: () => { calls.changed++; calls.callbacks.push(label) } }); effects.splice(0).forEach((effect) => effect()); return tree }
  render()
  return { controls, calls, render, async ready() { await settle(); render(); await settle() }, unmount() { slots.forEach((slot) => slot?.cleanup?.()) } }
}
function elements(tree) { if (Array.isArray(tree)) return tree.flatMap(elements); return tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : [] }
function text(tree) { if (Array.isArray(tree)) return tree.map(text).join(""); return tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }
function button(page, label) { const node = elements(page.render()).find((item) => item.type === "button" && text(item).includes(label)); assert.ok(node); return node }

test("report bookmark reads first and requires explicit desired-state save", async () => {
  const page = mount(); await page.ready()
  assert.equal(page.calls.read.length, 1)
  assert.equal(page.calls.write.length, 0)
  await button(page, "收藏报告").props.onClick(); await page.ready()
  assert.equal(page.calls.write[0].desired, true)
  assert.equal(page.calls.write[0].token, "token")
  assert.equal(page.calls.changed, 1)
  assert.match(text(page.render()), /取消收藏/)
})

test("unknown write outcome blocks another mutation until explicit query reconciliation", async () => {
  const page = mount(); await page.ready()
  page.controls.writeError = new Error("timeout")
  await button(page, "收藏报告").props.onClick(); await page.ready()
  assert.match(text(page.render()), /尚未确认|先查询/)
  assert.equal(page.calls.write.length, 1)
  const toggle = button(page, "收藏报告")
  assert.equal(toggle.props.disabled, true)
  await toggle.props.onClick()
  assert.equal(page.calls.write.length, 1)
  page.controls.view = { ...empty, bookmarked: true, created_at: "2026-10-22T00:00:00Z" }
  await button(page, "查询收藏状态").props.onClick(); await page.ready()
  assert.equal(button(page, "取消收藏").props.disabled, false)
  assert.equal(page.calls.changed, 1)
})

test("same-frame double click does not duplicate writes", async () => {
  const page = mount(); await page.ready()
  let finish; page.controls.deferredWrite = new Promise((resolve) => { finish = resolve })
  const click = button(page, "收藏报告").props.onClick
  const first = click(); const second = click()
  assert.equal(page.calls.write.length, 1)
  finish({ ...empty, bookmarked: true, created_at: "2026-10-22T00:00:00Z" }); await Promise.all([first, second]); await page.ready()
  assert.equal(page.calls.changed, 1)
})

test("late save and reconciliation refresh the latest parent filters", async () => {
  for (const reconcile of [false, true]) {
    const page = mount(); await page.ready()
    let finish
    if (reconcile) {
      page.controls.writeError = new Error("timeout")
      button(page, "收藏报告").props.onClick(); await page.ready()
      page.controls.deferredRead = new Promise((resolve) => { finish = resolve })
      button(page, "查询收藏状态").props.onClick()
    } else {
      page.controls.deferredWrite = new Promise((resolve) => { finish = resolve })
      button(page, "收藏报告").props.onClick()
    }
    page.controls.callbackLabel = "latest-analysis"
    page.render()
    finish({ ...empty, bookmarked: true, created_at: "2026-10-22T00:00:00Z" })
    await page.ready()
    assert.deepEqual(page.calls.callbacks, ["latest-analysis"])
  }
})

test("changed market or login revision cannot apply a late write or callback", async () => {
  for (const patch of [{ user: { id: "user", market_type: "futures" } }, { sessionRevision: 2 }, { accessToken: "other" }]) {
    const page = mount(); await page.ready()
    let finish; page.controls.deferredWrite = new Promise((resolve) => { finish = resolve })
    const saving = button(page, "收藏报告").props.onClick()
    page.controls.auth = { ...page.controls.auth, ...patch }
    finish({ ...empty, bookmarked: true, created_at: "2026-10-22T00:00:00Z" }); await saving; await page.ready()
    assert.equal(page.calls.changed, 0)
    assert.doesNotMatch(text(page.render()), /取消收藏/)
  }
})

test("switching selected report or unmounting aborts reads and invalidates old buttons", async () => {
  const page = mount(); await page.ready()
  const old = button(page, "收藏报告").props.onClick
  const signal = page.calls.read[0].signal
  page.controls.jobId = "22222222-2222-4222-8222-222222222222"
  page.render(); await page.ready()
  assert.equal(signal.aborted, true)
  await old()
  assert.equal(page.calls.write.length, 0)
  page.unmount()
  assert.equal(page.calls.read.at(-1).signal.aborted, true)
})

test("an unconfirmed bookmark write survives navigation, carries no token and is never auto-rewritten", async () => {
  const storage = memoryStorage()
  const page = mount({ storage }); await page.ready()
  page.controls.writeError = new Error("timeout")
  await button(page, "收藏报告").props.onClick(); await page.ready()
  const stored = JSON.parse(storage.getItem("stock-pending-writes:user:virtual"))
  assert.equal(stored.length, 1)
  assert.equal(stored[0].kind, "report-bookmark")
  assert.equal(JSON.parse(stored[0].signature).id, id)
  assert.equal(JSON.parse(stored[0].signature).bookmarked, true)
  assert.ok(stored[0].idempotency_key)
  assert.ok(stored[0].created_at)
  // 注册表只存那五个字段，token 绝不落盘
  assert.deepEqual(Object.keys(stored[0]).sort(), ["created_at", "idempotency_key", "kind", "signature"])
  assert.doesNotMatch(JSON.stringify(stored), /token/)
  page.unmount()

  const returned = mount({ storage }); await returned.ready()
  assert.match(text(returned.render()), /未确认/)
  // 回到页面只是提示，绝不替用户重写
  assert.equal(returned.calls.write.length, 0)
  returned.unmount()
})

test("an unconfirmed bookmark whose registration also failed says so instead of promising cross-page recovery", async () => {
  // 写已经发出且结果未知，此刻**必须**登记；登记也失败（注册表已满）时不能仍宣称
  // "离开页面不丢失"——那条记录根本没留下，用户会以为换页还能恢复。
  const storage = memoryStorage({
    "stock-pending-writes:user:virtual": JSON.stringify(Array.from({ length: 8 }, (_, position) =>
      ({ kind: "report-bookmark", idempotency_key: `key-${position}`, signature: JSON.stringify({ id: `other-${position}`, bookmarked: true }), created_at: "2026-10-26T00:00:00.000Z" }))),
  })
  const page = mount({ storage }); await page.ready()
  page.controls.writeError = new Error("network uncertain")
  await button(page, "收藏报告").props.onClick(); await page.ready()
  assert.equal(page.calls.write.length, 1)
  const shown = text(page.render())
  assert.match(shown, /保存结果尚未确认/)
  assert.match(shown, /未能登记|无法登记|尚未发送/)
  // 满载绝不驱逐任何一条别人的未确认写
  assert.equal(JSON.parse(storage.getItem("stock-pending-writes:user:virtual")).length, 8)
  page.unmount()
})

test("a confirmed bookmark state clears the unconfirmed write", async () => {
  const storage = memoryStorage()
  const page = mount({ storage }); await page.ready()
  page.controls.writeError = new Error("timeout")
  await button(page, "收藏报告").props.onClick(); await page.ready()
  // 前置：失败确实留下了未确认条目，否则下面的「已清除」是假绿
  assert.equal(JSON.parse(storage.getItem("stock-pending-writes:user:virtual")).length, 1)
  page.unmount()

  const returned = mount({ storage }); await returned.ready()
  page.controls.view = { ...empty, bookmarked: true, created_at: "2026-10-22T00:00:00Z" }
  returned.controls.view = { ...empty, bookmarked: true, created_at: "2026-10-22T00:00:00Z" }
  await button(returned, "查询收藏状态").props.onClick(); await returned.ready()
  assert.equal(storage.getItem("stock-pending-writes:user:virtual"), null)
  assert.doesNotMatch(text(returned.render()), /未确认/)
  returned.unmount()
})

test("another login scope or another write kind never surfaces as a bookmark notice", async () => {
  const storage = memoryStorage({
    "stock-pending-writes:someone-else:live": JSON.stringify([{ kind: "report-bookmark", idempotency_key: "k1", signature: JSON.stringify({ id, bookmarked: true }), created_at: "2026-10-26T00:00:00.000Z" }]),
    "stock-pending-writes:user:virtual": JSON.stringify([{ kind: "dataset-job", idempotency_key: "k2", signature: JSON.stringify({ idempotency_key: "k2" }), created_at: "2026-10-26T00:00:00.000Z" }]),
  })
  const page = mount({ storage }); await page.ready()
  assert.doesNotMatch(text(page.render()), /未确认/)
  assert.equal(page.calls.write.length, 0)
  page.unmount()
})

test("a pending bookmark entry whose report differs is not attributed to the selected report", async () => {
  const storage = memoryStorage({
    "stock-pending-writes:user:virtual": JSON.stringify([{ kind: "report-bookmark", idempotency_key: "k3",
      signature: JSON.stringify({ id: "33333333-3333-4333-8333-333333333333", bookmarked: true }), created_at: "2026-10-26T00:00:00.000Z" }]),
  })
  const page = mount({ storage }); await page.ready()
  assert.doesNotMatch(text(page.render()), /未确认/)
  page.unmount()
})
