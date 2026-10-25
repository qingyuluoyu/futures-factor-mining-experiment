import assert from "node:assert/strict"
import test from "node:test"
import { existsSync } from "node:fs"

globalThis.localStorage = { getItem: () => "qa-token" }
globalThis.window = { location: { href: "" } }
const filename = new URL("./stock-eod-status-api.ts", import.meta.url)
const states = ["disabled", "unavailable", "manual_only", "stopped", "before_window", "pending", "running", "closed", "completed", "partially_completed", "failed", "expired"]
const status = {
  status_version: "stock-eod-status-v1", server_time: "2026-10-19T04:50:00+08:00", timezone: "Asia/Shanghai", collection_window: "16:30", schedule_interval_seconds: 300,
  enabled: true, sync_enabled: true, worker_running: true, state: "before_window", message: "尚未到盘后采集窗口", today_job: null, latest_snapshot: null,
  verification: "stored_metadata_only_not_independent_certification",
}
const archive = {
  status_version: "stock-archive-status-v1", enabled: true, ready: true, worker_running: true,
  state: "running", message: "影子归档进行中", latest_job: {
    id: "301ba97e-4f55-4daa-b4a5-d4319ea8cf71", request_id: "0123456789abcdef0123456789abcdef",
    source_batch_id: "14831b03-e618-4f9d-a89b-f6db88f3c62b", source_data_version: "a".repeat(64), trade_date: "2026-10-16",
    status: "running", step: "archiving", attempts: 1, started_at: "2026-10-19T04:50:00+08:00",
    updated_at: "2026-10-19T04:50:00+08:00", deadline_at: "2026-10-19T04:51:30+08:00",
    finished_at: null, archive_batch_id: null, error_code: null,
  }, scope: "shadow_only_user_read_path_unchanged", source_calls: 0, model_calls: 0,
}
async function api() { assert.ok(existsSync(filename), "read-only stock EOD status API must exist"); return import(filename) }

test("catchup metadata remains bounded and cannot claim calendar certification or retry budgets", async () => {
  const { getStockEodStatusApi } = await api()
  const catchup = { policy_version: "stock-eod-catchup-v1", window_start: "2026-10-13", window_end: "2026-10-19", pending_dates: ["2026-10-19"], failed_dates: [], expired_dates: [], calendar_verified: false, max_dates_per_cycle: 1, max_source_calls_per_date: 3, message: "自然日待日历核验" }
  globalThis.fetch = async () => Response.json({ ...status, catchup })
  assert.deepEqual((await getStockEodStatusApi()).catchup, catchup)
  for (const patch of [{ calendar_verified: true }, { max_dates_per_cycle: 2 }, { pending_dates: Array(8).fill("2026-10-19") }, { pending_dates: ["2026-10-20"] }]) {
    globalThis.fetch = async () => Response.json({ ...status, catchup: { ...catchup, ...patch } })
    await assert.rejects(getStockEodStatusApi(), /同步状态响应格式异常/)
  }
})

test("status uses authenticated no-store GET, bounded timeout and caller cancellation", async () => {
  const { getStockEodStatusApi } = await api()
  let observed
  globalThis.fetch = async (path, options) => { observed = { path, options }; return Response.json(status) }
  const controller = new AbortController()
  assert.equal((await getStockEodStatusApi(controller.signal)).state, "before_window")
  assert.equal(observed.path, "/api/stock/market/sync-status")
  assert.equal(observed.options.method, "GET")
  assert.equal(observed.options.cache, "no-store")
  assert.equal(observed.options.headers.Authorization, "Bearer qa-token")
  assert.ok(observed.options.signal instanceof AbortSignal)
  controller.abort()
  assert.equal(observed.options.signal.aborted, true)
})

test("status validates every supported state and preserves snapshot/job evidence", async () => {
  const { getStockEodStatusApi } = await api()
  for (const state of states) {
    const payload = { ...status, state, today_job: { id: "job-1", trade_date: "2026-10-19", status: "running", step: "fetch", attempts: 1, started_at: "2026-10-19T16:30:00+08:00", deadline_at: "2026-10-19T16:35:00+08:00", finished_at: null, error_code: null }, latest_snapshot: { trade_date: "2026-10-16", data_version: "abc123", collected_at: "2026-10-18T04:00:00+00:00", provider: "teajoin", price_adjustment: "none", coverage: { expected: 5558, quoted: 5548, missing: 10, unknown: 19 }, raw_response_evidence: false } }
    globalThis.fetch = async () => Response.json(payload)
    assert.deepEqual(await getStockEodStatusApi(), payload)
  }
})

test("invalid status or evidence rejects rather than fabricating successful coverage", async () => {
  const { getStockEodStatusApi } = await api()
  for (const invalid of [{ state: "healthy" }, { enabled: "true" }, { server_time: "2026-10-19" }, { timezone: "UTC" }, { status_version: "future-v2" }, { verification: "certified" }, { schedule_interval_seconds: -1 }, { today_job: {} }, { latest_snapshot: { trade_date: "invalid" } }]) {
    globalThis.fetch = async () => Response.json({ ...status, ...invalid })
    await assert.rejects(getStockEodStatusApi(), /同步状态响应格式异常/)
  }
})

test("database unavailability preserves HTTP failure and never returns an empty success", async () => {
  const { getStockEodStatusApi } = await api()
  globalThis.fetch = async () => Response.json({ detail: "同步状态暂不可用" }, { status: 503 })
  await assert.rejects(getStockEodStatusApi(), (error) => error.status === 503 && /同步状态暂不可用/.test(error.message))
})

test("status reads have a finite timeout without retrying supplier work", async () => {
  const { getStockEodStatusApi } = await api()
  const originalTimeout = AbortSignal.timeout
  const deadline = new AbortController()
  let milliseconds, calls = 0
  AbortSignal.timeout = (duration) => { milliseconds = duration; return deadline.signal }
  globalThis.fetch = async (path, options) => { calls += 1; deadline.abort(new DOMException("timeout", "TimeoutError")); throw options.signal.reason }
  try { await assert.rejects(getStockEodStatusApi(), { name: "TimeoutError" }) } finally { AbortSignal.timeout = originalTimeout }
  assert.ok(milliseconds > 0 && milliseconds <= 20000)
  assert.equal(calls, 1)
})

test("archive status is optional for old servers and preserves complete shadow task evidence", async () => {
  const { getStockEodStatusApi } = await api()
  globalThis.fetch = async () => Response.json(status)
  assert.equal(Object.hasOwn(await getStockEodStatusApi(), "archive"), false)
  for (const state of ["disabled", "unavailable", "stopped", "idle", "running", "completed", "failed", "cancelled", "expired"]) {
    const payload = { ...status, archive: { ...archive, state } }
    globalThis.fetch = async () => Response.json(payload)
    assert.deepEqual(await getStockEodStatusApi(), payload)
  }
  for (const jobStatus of ["completed", "failed", "cancelled"]) {
    const payload = { ...status, archive: { ...archive, state: jobStatus, latest_job: { ...archive.latest_job, status: jobStatus, step: jobStatus, finished_at: "2026-10-19T04:50:30+08:00", archive_batch_id: jobStatus === "completed" ? "f5ebaa36-febc-4789-a2aa-f4c265cf7ba5" : null, error_code: jobStatus === "completed" ? null : "archive_expired" } } }
    globalThis.fetch = async () => Response.json(payload)
    assert.deepEqual(await getStockEodStatusApi(), payload)
  }
  const payload = { ...status, archive: { ...archive, state: "idle", latest_job: null } }
  globalThis.fetch = async () => Response.json(payload)
  assert.deepEqual(await getStockEodStatusApi(), payload)
})

test("present malformed archive status fails instead of appearing missing or idle", async () => {
  const { getStockEodStatusApi } = await api()
  const invalidArchives = [null, {}, "idle", { ...archive, status_version: "future-v2" }, { ...archive, enabled: "true" }, { ...archive, ready: null }, { ...archive, worker_running: 1 }, { ...archive, state: "healthy" }, { ...archive, message: "" }, { ...archive, scope: "certified" }, { ...archive, source_calls: 1 }, { ...archive, model_calls: -1 }, { ...archive, latest_job: {} }]
  for (const invalid of invalidArchives) {
    globalThis.fetch = async () => Response.json({ ...status, archive: invalid })
    await assert.rejects(getStockEodStatusApi(), /同步状态响应格式异常/)
  }
  for (const invalid of [{ id: "job-1" }, { source_batch_id: "source" }, { request_id: "" }, { source_data_version: "abc" }, { trade_date: "2026-02-30" }, { status: "idle" }, { step: "fetch" }, { attempts: 0 }, { attempts: 1.5 }, { started_at: null }, { updated_at: "2026-10-19" }, { deadline_at: "invalid" }, { finished_at: "bad" }, { archive_batch_id: "archive-1" }, { error_code: 500 }]) {
    globalThis.fetch = async () => Response.json({ ...status, archive: { ...archive, latest_job: { ...archive.latest_job, ...invalid } } })
    await assert.rejects(getStockEodStatusApi(), /同步状态响应格式异常/)
  }
})

test("archive task contract rejects attempt, request identity and inconsistent lifecycle evidence", async () => {
  const { getStockEodStatusApi } = await api()
  const completed = { ...archive.latest_job, status: "completed", step: "completed", finished_at: "2026-10-19T04:50:30+08:00", archive_batch_id: "f5ebaa36-febc-4789-a2aa-f4c265cf7ba5" }
  const failed = { ...archive.latest_job, status: "failed", step: "failed", finished_at: "2026-10-19T04:50:30+08:00", error_code: "archive_expired" }
  const cancelled = { ...failed, status: "cancelled", step: "cancelled", error_code: "archive_cancelled" }
  const invalidJobs = [
    { ...archive.latest_job, attempts: 2 }, { ...archive.latest_job, request_id: "a".repeat(31) },
    { ...archive.latest_job, request_id: "A".repeat(32) }, { ...archive.latest_job, request_id: "z".repeat(32) },
    { ...archive.latest_job, step: "completed" }, { ...archive.latest_job, finished_at: completed.finished_at },
    { ...archive.latest_job, archive_batch_id: completed.archive_batch_id }, { ...archive.latest_job, error_code: "archive_failed" },
    { ...completed, step: "archiving" }, { ...completed, finished_at: null },
    { ...completed, archive_batch_id: null }, { ...completed, error_code: "archive_failed" },
    { ...failed, step: "cancelled" }, { ...failed, finished_at: null }, { ...failed, archive_batch_id: completed.archive_batch_id }, { ...failed, error_code: null },
    { ...cancelled, step: "failed" }, { ...cancelled, finished_at: null }, { ...cancelled, archive_batch_id: completed.archive_batch_id }, { ...cancelled, error_code: null },
  ]
  for (const latest_job of invalidJobs) {
    globalThis.fetch = async () => Response.json({ ...status, archive: { ...archive, latest_job } })
    await assert.rejects(getStockEodStatusApi(), /同步状态响应格式异常/, JSON.stringify(latest_job))
  }
  for (const latest_job of [archive.latest_job, completed, failed, cancelled]) {
    const payload = { ...status, archive: { ...archive, state: "expired", latest_job } }
    globalThis.fetch = async () => Response.json(payload)
    assert.deepEqual(await getStockEodStatusApi(), payload)
  }
})
