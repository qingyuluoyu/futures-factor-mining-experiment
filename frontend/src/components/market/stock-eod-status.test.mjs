import assert from "node:assert/strict"
import test from "node:test"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import vm from "node:vm"
import ts from "typescript"

const filename = new URL("./stock-eod-status.tsx", import.meta.url)
const requireModule = createRequire(import.meta.url)
const status = {
  status_version: "stock-eod-status-v1", server_time: "2026-10-19T04:50:00+08:00", timezone: "Asia/Shanghai", collection_window: "16:30", schedule_interval_seconds: 300,
  enabled: true, sync_enabled: true, worker_running: true, state: "before_window", message: "未到16:30盘后窗口", today_job: null, latest_snapshot: null,
  verification: "stored_metadata_only_not_independent_certification",
}
const archive = {
  status_version: "stock-archive-status-v1", enabled: true, ready: true, worker_running: true,
  state: "running", message: "正在归档已存快照", latest_job: {
    id: "301ba97e-4f55-4daa-b4a5-d4319ea8cf71", request_id: "0123456789abcdef0123456789abcdef",
    source_batch_id: "14831b03-e618-4f9d-a89b-f6db88f3c62b", source_data_version: "a".repeat(64), trade_date: "2026-10-16",
    status: "running", step: "archiving", attempts: 1, started_at: "2026-10-19T04:50:00+08:00",
    updated_at: "2026-10-19T04:50:00+08:00", deadline_at: "2026-10-19T04:51:30+08:00", finished_at: null,
    archive_batch_id: null, error_code: null,
  }, scope: "shadow_only_user_read_path_unchanged", source_calls: 0, model_calls: 0,
}
function elements(tree) { return Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : [] }
function text(tree) { return Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }
function mount({ response = status, error = null, deferred = null } = {}) {
  assert.ok(existsSync(filename), "shared stock EOD status component must exist")
  const output = ts.transpileModule(readFileSync(filename, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const slots = [], effects = [], calls = []
  const controls = { response, error, deferred }
  let cursor = 0
  const dependencies = {
    react: {
      useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial; return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value }] },
      useRef(initial) { const index = cursor++; if (!(index in slots)) slots[index] = { current: initial }; return slots[index] },
      useEffect(callback, deps) { const index = cursor++; if (!(index in slots) || deps.some((value, position) => value !== slots[index].deps[position])) { slots[index]?.cleanup?.(); slots[index] = { deps }; effects.push(() => { slots[index].cleanup = callback() }) } },
    },
    "@/lib/stock-eod-status-api": { getStockEodStatusApi: async (signal) => { calls.push(signal); if (controls.error) throw controls.error; return controls.deferred ?? controls.response } },
  }
  const module = { exports: {} }
  vm.runInNewContext(output, { module, exports: module.exports, AbortController, Error, require: (name) => dependencies[name] ?? requireModule(name) })
  return { render(refreshKey = 0) { cursor = 0; return module.exports.StockEodStatus({ refreshKey }) }, calls, controls, async settle() { while (effects.length) effects.shift()(); await new Promise(setImmediate) }, unmount() { slots.forEach((slot) => slot?.cleanup?.()) } }
}

test("missed-day catchup is visible before today's close and never labeled confirmed trading gaps", async () => {
  const catchup = { pending_dates: ["2026-10-19"], failed_dates: ["2026-10-17"], expired_dates: [], window_start: "2026-10-13", window_end: "2026-10-19", max_dates_per_cycle: 1, max_source_calls_per_date: 3, message: "自然日逐日核验，失败不自动重试" }
  const page = mount({ response: { ...status, catchup } }); page.render(); await page.settle()
  const content = text(page.render())
  for (const phrase of ["2026-10-19", "2026-10-17", "待日历核验", "失败不自动重试"]) assert.ok(content.includes(phrase), phrase)
  assert.doesNotMatch(content, /已确认缺失交易日|今日收盘已更新/)
})

test("a cached recovery explains the extra manual event instead of pretending the first fetch succeeded", async () => {
  const snapshot = { trade_date: "2026-10-19", data_version: "fixture-version", collected_at: "2026-10-20T00:30:00+08:00", provider: "teajoin", price_adjustment: "none", coverage: { expected: 5558, quoted: 5545, missing: 13, unknown: 19 }, raw_response_evidence: true, recovery_applied: true }
  const page = mount({ response: { ...status, latest_snapshot: snapshot } }); page.render(); await page.settle()
  assert.match(text(page.render()), /人工恢复1次/)
  assert.match(text(page.render()), /原失败.*审计/)
  assert.doesNotMatch(text(page.render()), /已认证|全市场完整/)
})

test("initial read is cancellable and cancelling ignores a late success", async () => {
  let finish
  const page = mount({ deferred: new Promise((resolve) => { finish = resolve }) })
  assert.match(text(page.render()), /加载盘后同步状态/)
  await page.settle()
  elements(page.render()).find((item) => item.type === "button" && text(item) === "取消加载").props.onClick()
  assert.equal(page.calls[0].aborted, true)
  finish(status); await new Promise(setImmediate)
  assert.match(text(page.render()), /已取消/)
  assert.doesNotMatch(text(page.render()), /未到16:30盘后窗口/)
})

test("each scheduler state is explicit while missing snapshots never become today's close", async () => {
  const labels = { disabled: "服务停用", unavailable: "状态不可用", manual_only: "仅手动同步", stopped: "调度已停止", before_window: "未到盘后窗口", pending: "等待调度", running: "同步进行中", closed: "休市", completed: "同步完成", partially_completed: "部分完成", failed: "同步失败", expired: "任务已过期" }
  for (const [state, label] of Object.entries(labels)) {
    const page = mount({ response: { ...status, state } }); page.render(); await page.settle()
    assert.ok(text(page.render()).includes(label), `${state}: ${text(page.render())}`)
    assert.match(text(page.render()), /16:30/)
    assert.match(text(page.render()), /暂无已存盘后快照/)
    assert.match(text(page.render()), /不代表.*(认证|核验)|未经.*(认证|核验)/)
    assert.doesNotMatch(text(page.render()), /今日收盘价|今日行情已就绪|已认证/)
  }
})

test("old snapshot, coverage gaps and raw evidence are disclosed independently from task state", async () => {
  const snapshot = { trade_date: "2026-10-16", data_version: "abc123version", collected_at: "2026-10-18T04:00:00+00:00", provider: "teajoin", price_adjustment: "none", coverage: { expected: 5558, quoted: 5548, missing: 10, unknown: 19 }, raw_response_evidence: false }
  const job = { id: "job-1", trade_date: "2026-10-19", status: "failed", step: "validate", attempts: 2, started_at: null, deadline_at: null, finished_at: null, error_code: "UPSTREAM_TIMEOUT" }
  const page = mount({ response: { ...status, state: "failed", today_job: job, latest_snapshot: snapshot } }); page.render(); await page.settle()
  const tree = page.render()
  for (const value of ["2026-10-16", "5548", "5558", "10", "19", "teajoin", "abc123version", "原始响应证据缺失", "UPSTREAM_TIMEOUT", "validate"]) assert.ok(text(tree).includes(value), value)
  assert.doesNotMatch(text(tree), /已认证|最新交易日|今日行情已就绪/)
  page.controls.response = { ...status, latest_snapshot: { ...snapshot, raw_response_evidence: true } }
  elements(tree).find((item) => item.type === "button" && text(item) === "刷新状态").props.onClick()
  page.render(); await page.settle()
  assert.match(text(page.render()), /已存原始响应证据/)
  assert.match(text(page.render()), /不代表.*(认证|核验)|未经.*(认证|核验)/)
})

test("HTTP failure and timeout are actionable and retry recovers without a polling loop", async () => {
  const page = mount({ error: Object.assign(new Error("数据库暂不可用（503）"), { status: 503 }) }); page.render(); await page.settle()
  assert.match(text(page.render()), /数据库暂不可用/)
  assert.ok(elements(page.render()).find((item) => item.props?.role === "alert"))
  assert.doesNotMatch(text(page.render()), /暂无已存盘后快照/)
  page.controls.error = null
  elements(page.render()).find((item) => item.type === "button" && text(item) === "重试").props.onClick()
  page.render(); await page.settle()
  assert.equal(page.calls.length, 2)
  assert.match(text(page.render()), /未到盘后窗口/)
  page.render(); await page.settle(); assert.equal(page.calls.length, 2)
  const timeout = mount({ error: Object.assign(new Error("expired"), { name: "TimeoutError" }) }); timeout.render(); await timeout.settle()
  assert.match(text(timeout.render()), /超时/)
})

test("external revision refresh aborts previous read and unmount cancels remaining work", async () => {
  let finish
  const page = mount({ deferred: new Promise((resolve) => { finish = resolve }) }); page.render(); await page.settle()
  page.controls.deferred = null
  page.render(1); await page.settle()
  assert.equal(page.calls.length, 2)
  assert.equal(page.calls[0].aborted, true)
  finish({ ...status, state: "running" }); await new Promise(setImmediate)
  assert.match(text(page.render(1)), /未到盘后窗口/)
  page.unmount(); assert.equal(page.calls[1].aborted, true)
})

test("old servers disclose unavailable archive evidence without inferring completion", async () => {
  const page = mount(); page.render(); await page.settle()
  assert.match(text(page.render()), /旧服务未提供归档状态/)
  assert.doesNotMatch(text(page.render()), /归档完成|归档空闲/)
})

test("archive states, switches and worker readiness remain separate from EOD status", async () => {
  const labels = { disabled: "归档停用", unavailable: "归档不可用", stopped: "归档调度已停止", idle: "归档空闲", running: "归档进行中", completed: "归档完成", failed: "归档失败", cancelled: "归档已取消", expired: "归档已过期" }
  for (const [state, label] of Object.entries(labels)) {
    const page = mount({ response: { ...status, archive: { ...archive, state } } }); page.render(); await page.settle()
    const tree = page.render()
    for (const value of [label, "归档开关启用", "归档就绪", "归档worker运行", "未到盘后窗口", "仅影子归档", "未迁移旧qfq", "用户行情读路径未改变", "不代表独立核验或交易所认证"]) assert.ok(text(tree).includes(value), `${state}: ${value}`)
  }
  const page = mount({ response: { ...status, archive: { ...archive, enabled: false, ready: false, worker_running: false, state: "disabled", latest_job: null } } }); page.render(); await page.settle()
  for (const value of ["归档开关停用", "归档未就绪", "归档worker未运行", "暂无归档任务"]) assert.ok(text(page.render()).includes(value), value)
})

test("archive task shows source trade date, attempts and errors with trace IDs in details", async () => {
  const latest_job = { ...archive.latest_job, status: "failed", step: "failed", finished_at: "2026-10-19T04:51:31+08:00", error_code: "archive_expired" }
  const page = mount({ response: { ...status, archive: { ...archive, state: "expired", latest_job } } }); page.render(); await page.settle()
  const tree = page.render()
  const detail = elements(tree).find((item) => item.type === "details" && text(item).includes(latest_job.id))
  assert.ok(detail)
  for (const value of [latest_job.id, latest_job.request_id, latest_job.source_batch_id, latest_job.source_data_version, latest_job.started_at, latest_job.updated_at, latest_job.deadline_at, latest_job.finished_at, "2026-10-16", "尝试 1 次", "archive_expired"]) assert.ok(text(detail).includes(value), value)
  assert.match(text(tree), /归档已过期/)
  page.controls.response = { ...status, archive: { ...archive, state: "completed", latest_job: { ...latest_job, status: "completed", step: "completed", error_code: null, archive_batch_id: "f5ebaa36-febc-4789-a2aa-f4c265cf7ba5" } } }
  elements(tree).find((item) => item.type === "button" && text(item) === "刷新状态").props.onClick()
  page.render(); await page.settle()
  assert.equal(page.calls.length, 2)
  assert.match(text(page.render()), /归档完成/)
  assert.match(text(page.render()), /f5ebaa36-febc-4789-a2aa-f4c265cf7ba5/)
  assert.doesNotMatch(text(page.render()), /archive_expired/)
  page.render(); await page.settle(); assert.equal(page.calls.length, 2)
})

test("cancelled and failed reads never display fabricated archive state", async () => {
  let finish
  const page = mount({ deferred: new Promise((resolve) => { finish = resolve }) }); page.render(); await page.settle()
  elements(page.render()).find((item) => item.type === "button" && text(item) === "取消加载").props.onClick()
  finish({ ...status, archive }); await new Promise(setImmediate)
  assert.doesNotMatch(text(page.render()), /归档进行中|旧服务未提供归档状态|归档空闲/)
  const failure = mount({ error: new Error("同步状态响应格式异常") }); failure.render(); await failure.settle()
  assert.match(text(failure.render()), /响应格式异常/)
  assert.doesNotMatch(text(failure.render()), /归档进行中|旧服务未提供归档状态|归档空闲/)
})
