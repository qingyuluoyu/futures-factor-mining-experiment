import assert from "node:assert/strict"
import test from "node:test"

const api = await import("./stock-dataset-jobs-api.ts")
const id = "74490e6b-b979-4331-87d8-c25b809075dd"
const source = "5df7727e-dc73-428c-a526-83de24a1fcde"
const parameters = { source_ids: [source], start: "2026-01-05", end: "2026-01-06", warmup_start: "2026-01-05", pool_id: "qa-only", pool_version: "b".repeat(64), instruments: ["000001.SZ"] }
const item = { id, kind: "dataset", status: "pending", step: "queued", attempts: 0, request_id: "a".repeat(32), created_at: "2026-10-24T00:00:00Z", updated_at: "2026-10-24T00:00:00Z", started_at: null, finished_at: null, deadline_at: null, queue_expires_at: "2026-10-24T00:05:00Z", method_version: "stock-observation-manifest-v1", workflow_version: "stock-v2-dataset-job-v1", result: null, error_code: null }
const detail = { ...item, parameters }
const command = { idempotency_key: id, parameters }
async function withFetch(handler, callback) { const before = globalThis.fetch; globalThis.fetch = handler; try { await callback() } finally { globalThis.fetch = before } }

test("job reads are scoped, bounded, no-store and preserve disabled capabilities", async () => {
  const calls = []
  await withFetch(async (url, options) => { calls.push({ url, options }); return Response.json(url.endsWith("availability") ? { accepting: false, research_enabled: false, reason: "观察任务未开放" } : url.includes("?") ? { items: [item], total: 1, has_more: false } : detail) }, async () => {
    assert.equal((await api.getStockDatasetJobAvailability("token")).accepting, false)
    assert.equal((await api.listStockDatasetJobs({}, "token")).total, 1)
    assert.equal((await api.getStockDatasetJob(id, "token")).id, id)
    assert.equal(calls[0].options.headers.Authorization, "Bearer token")
    assert.equal(calls[0].options.cache, "no-store")
    assert.ok(calls[0].options.signal)
    await assert.rejects(api.listStockDatasetJobs({ limit: 101 }, "token"))
    await assert.rejects(api.getStockDatasetJob(id, ""), /登录/)
    assert.equal(calls.length, 3)
  })
})

test("job submit uses caller UUID key without automatic retry or unconfirmed construction", async () => {
  const calls = []
  await withFetch(async (url, options) => { calls.push({ url, options }); return Response.json(detail, { status: 202 }) }, async () => {
    assert.equal((await api.submitStockDatasetJob(command, "token")).id, id)
    assert.equal(calls[0].options.method, "POST")
    assert.equal(JSON.parse(calls[0].options.body).idempotency_key, id)
    assert.equal(calls[0].options.headers["Content-Type"], "application/json")
    for (const invalid of [{ ...command, owner_id: id }, { ...command, parameters: { ...parameters, source_ids: [source, source] } }, { ...command, max_runtime_seconds: 301 }]) {
      await assert.rejects(api.submitStockDatasetJob(invalid, "token"))
    }
    assert.equal(calls.length, 1)
  })
})

test("responses cannot change selected job identity, fixed parameters or execution qualification", async () => {
  await withFetch(async () => Response.json({ ...detail, id: source }), async () => { await assert.rejects(api.getStockDatasetJob(id, "token")) })
  for (const response of [{ ...detail, parameters: { ...parameters, pool_id: "changed" } }, { ...detail, status: "completed", finished_at: item.created_at, attempts: 1, started_at: item.created_at, result: { dataset_id: source, data_version: "c".repeat(64), qualification: "backtest_ready" } }]) {
    await withFetch(async () => Response.json(response), async () => { await assert.rejects(api.submitStockDatasetJob(command, "token")) })
  }
  await withFetch(async () => Response.json({ items: [item, item], total: 2, has_more: false }), async () => { await assert.rejects(api.listStockDatasetJobs({}, "token")) })
  await withFetch(async () => Response.json({ ...item, status: "running" }), async () => { await assert.rejects(api.getStockDatasetJob(id, "token")) })
})

test("cancellation requires a cancelled scoped receipt and failures retain trace without retry", async () => {
  let calls = 0
  await withFetch(async (url, options) => { calls++; assert.equal(options.method, "POST"); return Response.json({ ...detail, status: "cancelled", finished_at: item.created_at, error_code: "user_cancelled" }) }, async () => {
    assert.equal((await api.cancelStockDatasetJob(id, "token")).status, "cancelled")
    assert.equal(calls, 1)
  })
  await withFetch(async () => Response.json(detail), async () => { await assert.rejects(api.cancelStockDatasetJob(id, "token")) })
  calls = 0
  await withFetch(async () => { calls++; return Response.json({ detail: { code: "worker_unavailable", message: "执行器未启动", request_id: "trace", retryable: false } }, { status: 503 }) }, async () => {
    await assert.rejects(api.submitStockDatasetJob(command, "token"), (error) => error.status === 503 && error.requestId === "trace" && error.message === "执行器未启动")
    assert.equal(calls, 1)
  })
})

test("oversized and non-JSON job replies never become empty-success states", async () => {
  await withFetch(async () => new Response("x".repeat(1100000)), async () => { await assert.rejects(api.listStockDatasetJobs({}, "token"), /大小/) })
  await withFetch(async () => new Response("not json"), async () => { await assert.rejects(api.getStockDatasetJob(id, "token"), /JSON/) })
})
