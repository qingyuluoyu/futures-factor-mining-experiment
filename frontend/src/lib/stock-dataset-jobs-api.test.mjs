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

// --- research kinds (migration 021) ------------------------------------------------
// The observation client pinned ``kind``, both version fields and the parameters to
// the single dataset kind.  Migration 021 widened the persisted allowlist to six, so
// the day a research job exists the shared list and detail reject the whole response
// and the panel breaks for observation jobs too.  These pin the widened contract.
const RESEARCH_KINDS = ["research_ic", "backtest", "benchmark", "ml_train", "report"]
const RESEARCH_METHOD = "stock-research-task-v1"
const RESEARCH_WORKFLOW = "stock-v2-research-task-v1"
const reference = { dataset_id: id, data_version: "b".repeat(64), qualification: "research_only" }
const factors = { factor_ids: ["ret_20d"], custom_factors: [], action: "analysis", industry_neutral: false, mktcap_neutral: false, weight: "equal" }
const costs = { commission_rate: "0.0003", min_commission: "5", slippage_rate: "0.001", participation_rate: "0.1", commission_basis: "inclusive_exchange_fees" }
const index = { spec: { instrument_id: "000300.SH", kind: "price_index", total_return_basis: "not_applicable", currency: "CNY", market: "CN_A", timezone: "Asia/Shanghai", frequency: "1d", level_unit: "point", provider: "qa", source_reference: "qa", authorization_reference: "qa", methodology_reference: "qa", calendar_reference: "qa", data_version: "c".repeat(64), qualification: "unverified", anchor: "next_session_open" }, rows: [{ trade_date: "2026-01-05", open: "3000", close: "3010" }, { trade_date: "2026-01-06", open: "3010", close: "3020" }] }
const split = { train_start: "2026-01-05", train_end: "2026-01-20", validation_start: "2026-01-21", validation_end: "2026-02-03", test_start: "2026-02-04", test_end: "2026-02-10" }
const sessions = Array.from({ length: 45 }, (_, position) => `2026-${String(Math.floor(position / 28) + 1).padStart(2, "0")}-${String((position % 28) + 1).padStart(2, "0")}`)
const walkforward = { sessions, mode: "expanding", warmup_sessions: 0, train_sessions: 20, validation_sessions: 10, test_sessions: 5, fold_count: 2 }
const training = { n_estimators: 100, max_depth: 5, num_leaves: 31, min_child_samples: 20, learning_rate: 0.05, reg_alpha: 0, reg_lambda: 0, seed: 20261025, threads: 2, max_seconds: 120 }
const researchParameters = {
  research_ic: { kind: "research_ic", dataset: reference, factors, horizon: 20, groups: 5, decay_lags: [1, 5, 10, 20] },
  backtest: { kind: "backtest", dataset: reference, factors, pool_version: "b".repeat(64), start: "2026-01-05", end: "2026-02-10", top_n: 20, rebalance: "monthly", weight: "equal", initial_cash: "1000000.00", risk_free_annual: "0.000000", costs, qa_only: true },
  benchmark: { kind: "benchmark", dataset: reference, strategy_artifact_sha256: "d".repeat(64), index },
  ml_train: { kind: "ml_train", dataset: reference, factors, split: walkforward, training, qa_only: true },
  report: { kind: "report", dataset: reference, factors, split, horizon: 20, groups: 5, top_n: 20, training, qa_only: true },
}
const PATHS = { research_ic: "/ic/jobs", backtest: "/backtest/jobs", benchmark: "/benchmark/jobs", ml_train: "/ml/jobs", report: "/report/jobs" }
const SUBMITTERS = { research_ic: "submitStockResearchICJob", backtest: "submitStockBacktestJob", benchmark: "submitStockBenchmarkJob", ml_train: "submitStockMLTrainJob", report: "submitStockReportJob" }

function researchJob(kind, overrides = {}) {
  return { ...item, kind, method_version: RESEARCH_METHOD, workflow_version: RESEARCH_WORKFLOW, ...overrides }
}

test("research jobs read through the shared list and detail instead of breaking the whole page", async () => {
  const others = Array.from({ length: 6 }, (_, position) => `74490e6b-b979-4331-87d8-c25b809075${String(position).padStart(2, "0")}`)
  await withFetch(async () => Response.json({ items: [item, ...RESEARCH_KINDS.map((kind, position) => researchJob(kind, { id: others[position] }))], total: 6, has_more: false }), async () => {
    const page = await api.listStockDatasetJobs({ limit: 100 }, "token")
    assert.deepEqual(page.items.map((entry) => entry.kind), ["dataset", ...RESEARCH_KINDS])
  })
  for (const kind of RESEARCH_KINDS) {
    await withFetch(async () => Response.json({ ...researchJob(kind), parameters: researchParameters[kind] }), async () => {
      const detail = await api.getStockDatasetJob(id, "token")
      assert.equal(detail.kind, kind)
      assert.equal(detail.parameters.kind, kind)
      assert.equal(detail.method_version, RESEARCH_METHOD)
      assert.equal(detail.workflow_version, RESEARCH_WORKFLOW)
    })
  }
})

test("a job cannot claim another kind's versions, parameters or graded result", async () => {
  await withFetch(async () => Response.json({ ...item, method_version: RESEARCH_METHOD, workflow_version: RESEARCH_WORKFLOW }), async () => {
    await assert.rejects(api.getStockDatasetJob(id, "token"))
  })
  for (const kind of RESEARCH_KINDS) {
    await withFetch(async () => Response.json({ ...researchJob(kind), method_version: "stock-observation-manifest-v1", workflow_version: "stock-v2-dataset-job-v1", parameters: researchParameters[kind] }), async () => {
      await assert.rejects(api.getStockDatasetJob(id, "token"))
    })
    for (const foreign of RESEARCH_KINDS.filter((entry) => entry !== kind)) {
      await withFetch(async () => Response.json({ ...researchJob(kind), parameters: researchParameters[foreign] }), async () => {
        await assert.rejects(api.getStockDatasetJob(id, "token"))
      })
    }
    await withFetch(async () => Response.json({ ...researchJob(kind), parameters }), async () => { await assert.rejects(api.getStockDatasetJob(id, "token")) })
    await withFetch(async () => Response.json({ ...researchJob(kind), parameters: researchParameters[kind], status: "completed", attempts: 1, started_at: item.created_at, finished_at: item.created_at, result: { ...reference, qualification: "backtest_ready" } }), async () => {
      await assert.rejects(api.getStockDatasetJob(id, "token"))
    })
  }
})

test("every research kind submits to its own path with its own parameters", async () => {
  const calls = []
  await withFetch(async (url, options) => {
    calls.push({ url, options })
    const kind = RESEARCH_KINDS.find((entry) => url.endsWith(PATHS[entry]))
    return Response.json({ ...researchJob(kind), parameters: researchParameters[kind] }, { status: 202 })
  }, async () => {
    for (const kind of RESEARCH_KINDS) {
      const receipt = await api[SUBMITTERS[kind]]({ idempotency_key: id, parameters: researchParameters[kind] }, "token")
      assert.equal(receipt.id, id)
      assert.equal(receipt.kind, kind)
      assert.equal(receipt.parameters.kind, kind)
    }
    assert.deepEqual(calls.map((call) => call.url.replace(/^.*\/research\/v2/, "")), RESEARCH_KINDS.map((kind) => PATHS[kind]))
    assert.ok(calls.every((call) => call.options.method === "POST" && call.options.headers["Content-Type"] === "application/json"))
    assert.equal(JSON.parse(calls[0].options.body).kind, "research_ic")
    // A body may not carry a foreign kind's tag.
    await assert.rejects(api.submitStockResearchICJob({ idempotency_key: id, parameters: { ...researchParameters.research_ic, kind: "backtest" } }, "token"))
    await assert.rejects(api.submitStockResearchICJob({ idempotency_key: id, parameters: researchParameters.backtest }, "token"))
    // The observation route keeps its own schema.
    await assert.rejects(api.submitStockDatasetJob({ idempotency_key: id, parameters: researchParameters.research_ic }, "token"))
  })
  assert.equal(calls.length, RESEARCH_KINDS.length)
})

test("research money and rate fields stay exact decimal strings instead of becoming floats", async () => {
  await withFetch(async () => Response.json({ ...researchJob("backtest"), parameters: researchParameters.backtest }, { status: 202 }), async () => {
    const receipt = await api.submitStockBacktestJob({ idempotency_key: id, parameters: researchParameters.backtest }, "token")
    assert.equal(receipt.parameters.initial_cash, "1000000.00")
    assert.equal(receipt.parameters.costs.commission_rate, "0.0003")
  })
  for (const invalid of [
    { ...researchParameters.backtest, initial_cash: 1000000 },
    { ...researchParameters.backtest, initial_cash: "1e14" },
    { ...researchParameters.backtest, initial_cash: "-1.00" },
    { ...researchParameters.backtest, risk_free_annual: "2.000000" },
    { ...researchParameters.backtest, costs: { ...costs, participation_rate: "0.5" } },
    { ...researchParameters.backtest, start: "2026-02-10", end: "2026-01-05" },
    { ...researchParameters.backtest, rebalance: "yearly" },
    { ...researchParameters.backtest, qa_only: false },
  ]) { await assert.rejects(api.submitStockBacktestJob({ idempotency_key: id, parameters: invalid }, "token"), `应当拒绝 ${JSON.stringify(invalid).slice(0, 60)}`) }
})

test("research factor, split and index declarations must be internally consistent", async () => {
  for (const invalid of [
    { ...factors, factor_ids: [], custom_factors: [] },
    { ...factors, factor_ids: ["ret_20d", "ret_20d"] },
    { ...factors, factor_ids: ["not_a_factor"] },
    { ...factors, custom_factors: [{ id, revision: 1, expression_version: "b".repeat(64) }, { id, revision: 1, expression_version: "b".repeat(64) }] },
  ]) { await assert.rejects(api.submitStockResearchICJob({ idempotency_key: id, parameters: { ...researchParameters.research_ic, factors: invalid } }, "token")) }
  for (const invalid of [
    { ...researchParameters.research_ic, decay_lags: [5, 1, 5] },
    { ...researchParameters.research_ic, decay_lags: [0, 1] },
    { ...researchParameters.research_ic, horizon: 0 },
    { ...researchParameters.research_ic, groups: 1 },
  ]) { await assert.rejects(api.submitStockResearchICJob({ idempotency_key: id, parameters: invalid }, "token")) }
  // The walk-forward axis must be explicit, unique, ascending and long enough.
  for (const invalid of [
    { ...walkforward, sessions: sessions.slice(0, 10) },
    { ...walkforward, sessions: [...sessions].reverse() },
    { ...walkforward, sessions: [...sessions, sessions[0]] },
    { ...walkforward, sessions: "2026-01-05" },
  ]) { await assert.rejects(api.submitStockMLTrainJob({ idempotency_key: id, parameters: { ...researchParameters.ml_train, split: invalid } }, "token")) }
  for (const invalid of [
    { ...split, validation_start: "2026-01-10" },
    { ...split, test_end: "2026-01-01" },
  ]) { await assert.rejects(api.submitStockReportJob({ idempotency_key: id, parameters: { ...researchParameters.report, split: invalid } }, "token")) }
  for (const invalid of [
    { ...index, spec: { ...index.spec, kind: "total_return_index" } },
    { ...index, spec: { ...index.spec, total_return_basis: "gross_reinvested" } },
    { ...index, spec: { ...index.spec, instrument_id: "600000.SH" } },
    { ...index, rows: [{ trade_date: "2026-01-05" }] },
    { ...index, rows: [{ trade_date: "2026-01-06" }, { trade_date: "2026-01-05" }] },
  ]) { await assert.rejects(api.submitStockBenchmarkJob({ idempotency_key: id, parameters: { ...researchParameters.benchmark, index: invalid } }, "token")) }
  for (const invalid of [
    { ...training, threads: 4 },
    { ...training, num_leaves: 64, max_depth: 3 },
    { ...training, learning_rate: 0 },
    { ...training, n_estimators: 501 },
  ]) { await assert.rejects(api.submitStockMLTrainJob({ idempotency_key: id, parameters: { ...researchParameters.ml_train, training: invalid } }, "token")) }
})

test("research jobs keep the per-kind runtime ceilings the backend enforces", async () => {
  const ceilings = { research_ic: 900, backtest: 900, benchmark: 900, ml_train: 900, report: 900 }
  for (const kind of RESEARCH_KINDS) {
    await assert.rejects(api[SUBMITTERS[kind]]({ idempotency_key: id, parameters: researchParameters[kind], max_runtime_seconds: 901 }, "token"))
    await assert.rejects(api[SUBMITTERS[kind]]({ idempotency_key: id, parameters: researchParameters[kind], max_runtime_seconds: 29 }, "token"))
    await assert.rejects(api[SUBMITTERS[kind]]({ idempotency_key: id, parameters: researchParameters[kind], max_queue_seconds: 901 }, "token"))
    assert.ok(ceilings[kind] <= 900)
  }
  await assert.rejects(api.submitStockDatasetJob({ idempotency_key: id, parameters, max_runtime_seconds: 900 }, "token"))
})
