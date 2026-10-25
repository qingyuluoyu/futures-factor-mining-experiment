import assert from "node:assert/strict"
import { existsSync } from "node:fs"
import test from "node:test"

const target = new URL("./stock-ml-models-api.ts", import.meta.url)
const api = existsSync(target) ? await import(target.href) : {}
const id = "b6372fe1-e9ee-4d12-b09c-929922f1d7e3"
const sha = "a".repeat(64)
const dataset = { dataset_id: id, data_version: sha, qualification: "observation_only" }
const parameters = { n_estimators: 10, max_depth: 3, num_leaves: 7, min_child_samples: 5, learning_rate: 0.05, reg_alpha: 0, reg_lambda: 0, seed: 20261025, threads: 2, max_seconds: 120 }
const summary = { id, dataset, state: "ready", validation_status: "qa_only", created_at: "2026-10-25T01:00:00Z", fold_count: 1, feature_names: ["quality"], holdout_registry_verified: false }
const model = { method_version: "stock-native-lambdarank-v1", scope: { owner_id: id, trading_mode: "virtual" }, dataset, parameters,
  runtime: { lightgbm: "4.7.0", python: "3.12", platform: "Windows", numpy: "2", pandas: "2" }, feature_order: ["quality"], feature_versions: { quality: sha }, training_sha256: sha, validation_sha256: sha, model_sha256: sha,
  training_knowledge_cutoff: "2026-01-29", created_at: summary.created_at, trees_used: 10, elapsed_seconds: 0.5, feature_gain: { quality: 1 }, validation_status: "qa_only", holdout_registry_verified: false, model_api_calls: 0 }
const detail = { ...summary, parameters, folds: [{ fold_id: sha, split: { train_start: "2025-01-01", train_end: "2025-12-31", validation_start: "2026-01-01", validation_end: "2026-01-30", test_start: "2026-02-01", test_end: "2026-02-28" }, model }], report_sha256: sha, limitations: ["QA数据，测试未封存"] }
async function response(payload, run, status = 200) {
  const original = globalThis.fetch, calls = []
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return Response.json(payload, { status }) }
  try { await run(calls) } finally { globalThis.fetch = original }
}

test("models client is read only, explicit and authenticates uncached bounded requests", async () => {
  assert.equal(typeof api.getStockModelAvailability, "function", "model history client exists")
  await response({ listing_enabled: false, training_enabled: false, reason: "本人模型历史未开放" }, async (calls) => {
    const value = await api.getStockModelAvailability("token")
    assert.equal(value.listing_enabled, false)
    assert.match(calls[0].url, /\/api\/stock\/research\/v2\/models\/availability$/)
    assert.equal(calls[0].options.method, "GET")
    assert.equal(calls[0].options.cache, "no-store")
    assert.equal(calls[0].options.headers.Authorization, "Bearer token")
    assert.ok(calls[0].options.signal)
    await assert.rejects(api.getStockModelAvailability(""), /登录/)
    assert.equal(calls.length, 1)
  })
})

test("real schema reads native version, parameters, fold date ranges and feature gains", async () => {
  assert.equal(typeof api.getStockModel, "function")
  await response(detail, async () => { const result = await api.getStockModel(id, "token"); assert.equal(result.folds[0].model.runtime.lightgbm, "4.7.0"); assert.equal(result.parameters.num_leaves, 7) })
  await response({ kind: "stock-walk-forward-calculation-v1", report: { evaluation: null, gain: [0.1] } }, async () => { assert.equal((await api.getStockModelReport(id, "token")).report.evaluation, null) })
})

test("strict schemas reject unknown fields, unsafe numbers, impossible calendar dates and inconsistent models", async () => {
  assert.ok(api.stockModelDetailSchema)
  for (const mutate of [
    (d) => { d.unknown = true }, (d) => { d.state = "trained" }, (d) => { d.created_at = 1700000000 },
    (d) => { d.parameters.num_leaves = 63 }, (d) => { d.folds[0].model.feature_gain.quality = Infinity },
    (d) => { d.folds[0].split.test_end = "2026-02-30" }, (d) => { d.fold_count = 2 },
    (d) => { d.folds[0].model.feature_versions = { value: sha } },
    (d) => { d.folds[0].model.dataset.data_version = "b".repeat(64) },
    (d) => { d.folds[0].model.training_knowledge_cutoff = "2026-02-01" },
    (d) => { d.holdout_registry_verified = true }, (d) => { d.folds[0].model.parameters.seed = 1 },
  ]) { const d = JSON.parse(JSON.stringify(detail)); mutate(d); assert.equal(api.stockModelDetailSchema.safeParse(d).success, false, String(mutate)) }
})

test("page checks duplicate ids, pagination totals and offsets but permits empty later-page recovery", async () => {
  assert.equal(typeof api.listStockModels, "function")
  for (const page of [{ items: [summary, summary], total: 2, has_more: false }, { items: [summary], total: 0, has_more: false }, { items: [summary], total: 21, has_more: true }]) {
    await response(page, async () => { await assert.rejects(api.listStockModels({ limit: 20, offset: 0 }, "token"), /分页/) })
  }
  await response({ items: [], total: 1, has_more: false }, async (calls) => {
    assert.equal((await api.listStockModels({ limit: 20, offset: 20 }, "token")).items.length, 0)
    await assert.rejects(api.listStockModels({ offset: 100001 }, "token"))
    assert.equal(calls.length, 1)
  })
})

test("detail never accepts another model identity and errors are classified without automatic retries", async () => {
  assert.equal(typeof api.getStockModel, "function")
  await response(detail, async () => { await assert.rejects(api.getStockModel("d6372fe1-e9ee-4d12-b09c-929922f1d7e3", "token"), /模型/) })
  for (const status of [401, 403, 404, 422, 503]) {
    await response({ detail: { code: "ml_unavailable", message: "模型暂不可读", request_id: "trace", retryable: true } }, async (calls) => {
      await assert.rejects(api.getStockModel(id, "token"), (error) => error.status === status && error.requestId === "trace" && error.retryable === (status === 503))
      assert.equal(calls.length, 1)
    }, status)
  }
})

test("response bound and malformed service errors preserve HTTP identity", async () => {
  assert.equal(typeof api.getStockModelAvailability, "function")
  const original = globalThis.fetch
  try {
    globalThis.fetch = async () => new Response("<html>gateway</html>", { status: 503 })
    await assert.rejects(api.getStockModelAvailability("token"), (error) => error.status === 503 && error.retryable)
    globalThis.fetch = async () => new Response("x".repeat(9 * 1024 * 1024))
    await assert.rejects(api.getStockModelAvailability("token"), /大小/)
  } finally { globalThis.fetch = original }
})
