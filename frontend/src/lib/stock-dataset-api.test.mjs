import assert from "node:assert/strict"
import test from "node:test"

const api = await import("./stock-dataset-api.ts")
const reference = { dataset_id: "74490e6b-b979-4331-87d8-c25b809075dd", data_version: "a".repeat(64), qualification: "observation_only" }
const item = { ...reference, artifact_state: "ready", integrity: "not_checked", created_at: "2026-10-23T05:00:00Z" }
const description = { reference, manifest_version: "stock-observation-manifest-v1", start: "2026-01-05", end: "2026-01-06", warmup_start: "2026-01-05", pool_id: "qa-only", pool_version: "b".repeat(64), instruments: ["000001.SZ"], sources: [{ source_id: "5df7727e-dc73-428c-a526-83de24a1fcde", sha256: "c".repeat(64), byte_size: 1024, row_count: 2, kind: "daily" }], integrity: "manifest_verified_sources_not_checked", research_enabled: false, limitations: ["仅清单，不代表实际覆盖"] }

async function withFetch(handler, callback) { const original = globalThis.fetch; globalThis.fetch = handler; try { await callback() } finally { globalThis.fetch = original } }

test("dataset metadata requests use scoped auth, no-store and exact immutable reference without automatic retry", async () => {
  const calls = []
  await withFetch(async (url, options) => { calls.push({ url, options }); return Response.json(description) }, async () => {
    const result = await api.getStockDatasetDescription(reference, "token")
    assert.deepEqual(result.reference, reference)
    assert.match(calls[0].url, new RegExp(`data_version=${reference.data_version}`))
    assert.equal(calls[0].options.headers.Authorization, "Bearer token")
    assert.equal(calls[0].options.cache, "no-store")
    assert.ok(calls[0].options.signal)
    await assert.rejects(api.getStockDatasetDescription(reference, ""), /登录/)
    assert.equal(calls.length, 1)
  })
})

test("version identity mismatches, false health claims and invalid pagination are rejected", async () => {
  await withFetch(async () => Response.json({ ...description, reference: { ...reference, data_version: "f".repeat(64) } }), async () => {
    await assert.rejects(api.getStockDatasetDescription(reference, "token"), /版本/)
  })
  await withFetch(async () => Response.json({ ...description, integrity: "all_sources_certified" }), async () => {
    await assert.rejects(api.getStockDatasetDescription(reference, "token"))
  })
  await withFetch(async () => Response.json({ items: [item], total: 0, has_more: false }), async () => {
    await assert.rejects(api.listStockDatasets({}, "token"), /分页/)
  })
  await withFetch(async () => Response.json({ items: [item, item], total: 2, has_more: false }), async () => {
    await assert.rejects(api.listStockDatasets({}, "token"), /分页/)
  })
})

test("availability preserves disabled computation and failures surface request ids without retries", async () => {
  await withFetch(async () => Response.json({ catalog_enabled: true, detail_enabled: false, inspection_enabled: false, research_enabled: false, parquet_enabled: false, reason: "产物未配置" }), async () => {
    const availability = await api.getStockDatasetAvailability("token")
    assert.equal(availability.research_enabled, false)
    assert.equal(availability.detail_enabled, false)
  })
  let calls = 0
  await withFetch(async () => { calls++; return Response.json({ detail: { code: "dataset_version_conflict", message: "固定版本不匹配", request_id: "trace-id", retryable: false } }, { status: 409 }) }, async () => {
    await assert.rejects(api.getStockDatasetDescription(reference, "token"), (error) => error.status === 409 && error.requestId === "trace-id" && error.message === "固定版本不匹配")
    assert.equal(calls, 1)
  })
})

test("oversized or non-JSON replies never get treated as empty datasets", async () => {
  await withFetch(async () => new Response("x".repeat(1100000)), async () => { await assert.rejects(api.listStockDatasets({}, "token"), /大小/) })
  await withFetch(async () => new Response("<html>unexpected</html>"), async () => { await assert.rejects(api.listStockDatasets({}, "token"), /JSON/) })
})
