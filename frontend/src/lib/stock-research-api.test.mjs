import assert from "node:assert/strict"
import test from "node:test"

const api = await import("./stock-research-api.ts")
const id = "11111111-1111-4111-8111-111111111111"
const job = { id, kind: "analysis", title: "测试", status: "pending", step: "queued", progress: 0, request_id: "trace", attempts: 0, created_at: "2026-10-18T00:00:00Z", updated_at: "2026-10-18T00:00:00Z", started_at: null, finished_at: null, method_version: "v1", workflow_version: "v1", data_version: null, has_result: false, error: null }
const draft = { idempotency_key: id, kind: "analysis", parameters: { factor_id: "ret_20d", start_date: "2026-07-13", end_date: "2026-09-11" } }
const metadata = { market: "stock", research_only: true, research_job_id: id, data_version: "a".repeat(64), data_manifest: { source: "local" }, method_version: "v1", workflow_version: "jobs-v1", validation_status: "research_only", validation_limits: ["历史修订待验证"] }
const analysisReport = { ...metadata, factor_id: "ret_20d", label: "test", group: "动量", direction: "high", decay: [], quantile_returns: [], long_short_nav: [], ic: { ic_mean: 0.01, ic_std: 0.1, ic_ir: null, rank_ic_mean: 0.02, rank_ic_ir: null, ic_positive_ratio: 0.5, periods: 3 } }

test("saved neutralization policy is validated without adding current policy to older results", async () => {
  const policy = { version: "neutralization-identifiability-v1", min_industry_observations: 2, min_residual_degrees_of_freedom: 1, requires_full_column_rank: true, insufficient_sample: "exclude_without_imputation" }
  globalThis.fetch = async () => Response.json({ ...analysisReport, neutralization_policy: policy })
  assert.deepEqual((await api.getStockResearchResult(id, "analysis", "token")).neutralization_policy, policy)
  globalThis.fetch = async () => Response.json(analysisReport)
  assert.equal((await api.getStockResearchResult(id, "analysis", "token")).neutralization_policy, undefined)
  for (const patch of [{ min_industry_observations: 1 }, { requires_full_column_rank: false }, { min_residual_degrees_of_freedom: 0 }]) {
    globalThis.fetch = async () => Response.json({ ...analysisReport, neutralization_policy: { ...policy, ...patch } })
    await assert.rejects(api.getStockResearchResult(id, "analysis", "token"))
  }
})

test("create validates strict parameters and binds original idempotency and token", async () => {
  const requests = []
  globalThis.fetch = async (url, options) => { requests.push({ url, options }); return Response.json(job, { status: 202 }) }
  await api.createStockResearchJob(draft, "bound-token")
  assert.equal(requests[0].options.headers.Authorization, "Bearer bound-token")
  assert.equal(JSON.parse(requests[0].options.body).idempotency_key, id)
  assert.ok(requests[0].options.signal instanceof AbortSignal)
  await assert.rejects(api.createStockResearchJob({ ...draft, parameters: { ...draft.parameters, user_id: id } }, "bound-token"))
  assert.equal(requests.length, 1)
})

test("shared dates accept backend formats and blank end, reject inverted/future/oversized periods", () => {
  for (const start_date of ["20260713", "2026-07-13", "2026/07/13"]) assert.ok(api.newResearchCreateSchema.safeParse({ ...draft, parameters: { ...draft.parameters, start_date } }).success)
  assert.ok(api.newResearchCreateSchema.safeParse({ ...draft, parameters: { ...draft.parameters, end_date: "" } }).success)
  for (const parameters of [{ start_date: "2026-09-11", end_date: "2026-07-13" }, { start_date: "2026-02-30", end_date: "" }, { start_date: "2000-01-01", end_date: "2026-01-01" }, { start_date: "2026-01-01", end_date: "2099-01-01" }]) assert.equal(api.newResearchCreateSchema.safeParse({ ...draft, parameters: { factor_id: "ret_20d", ...parameters } }).success, false)
})

test("network uncertainty never automatically resubmits paid work", async () => {
  let calls = 0
  globalThis.fetch = async () => { calls++; throw new Error("network") }
  await assert.rejects(api.createStockResearchJob(draft, "token"), /network/)
  assert.equal(calls, 1)
})

test("job/result/status schema rejects invalid state, nonfinite results and result identity mismatch", async () => {
  assert.equal(api.researchJobSchema.safeParse({ ...job, status: "invented" }).success, false)
  globalThis.fetch = async () => Response.json({ ...job, status: "completed", has_result: false })
  await assert.rejects(api.getStockResearchJob(id, "token"))
  globalThis.fetch = async () => Response.json({ market: "stock", research_only: true, research_job_id: id, data_version: "a".repeat(64), data_manifest: {}, factor_id: "ret_20d", label: "test", group: "动量", direction: "high", decay: [], quantile_returns: [], long_short_nav: [], ic: { ic_mean: null } })
  await assert.rejects(api.getStockResearchResult(id, "analysis", "token"))
})

test("list is bounded, errors preserve status and cancellation has no client identity body", async () => {
  let captured
  globalThis.fetch = async (url, options) => { captured = { url, options }; return Response.json(job) }
  await api.cancelStockResearchJob(id, "token")
  assert.equal(captured.options.method, "POST")
  assert.equal(captured.options.body, undefined)
  await assert.rejects(api.listStockResearchJobs({ limit: 51, offset: 0 }, "token"))
  globalThis.fetch = async () => Response.json({ detail: "不可用" }, { status: 503 })
  await assert.rejects(api.getStockResearchAvailability("token"), (error) => error.status === 503 && /不可用/.test(error.message))
})

test("session recovery keys use JWT trading mode and refuse mismatched users", () => {
  const token = (payload) => `header.${Buffer.from(JSON.stringify(payload)).toString("base64url")}.sig`
  assert.equal(api.stockResearchScope("user-a", token({ sub: "user-a", trading_mode: "virtual" })), "user-a:virtual")
  assert.equal(api.stockResearchScope("user-b", token({ sub: "user-a", trading_mode: "live" })), null)
  assert.equal(api.stockResearchScope("user-a", "bad-token"), null)
  assert.equal(api.isActiveResearchJob(job), true)
  assert.equal(api.isActiveResearchJob({ ...job, status: "failed" }), false)
})

test("successful reports preserve financial fields and require stock scope and version provenance", async () => {
  globalThis.fetch = async () => Response.json(analysisReport)
  const result = await api.getStockResearchResult(id, "analysis", "token")
  assert.equal(result.ic.ic_ir, null)
  assert.equal(result.data_manifest.source, "local")
  for (const field of ["market", "research_only", "method_version", "workflow_version"]) {
    const malformed = { ...analysisReport }; delete malformed[field]
    globalThis.fetch = async () => Response.json(malformed)
    await assert.rejects(api.getStockResearchResult(id, "analysis", "token"))
  }
  globalThis.fetch = async () => Response.json({ ...analysisReport, research_job_id: "22222222-2222-4222-8222-222222222222" })
  await assert.rejects(api.getStockResearchResult(id, "analysis", "token"), /不匹配/)
})

test("backtest response accepts nullable Calmar and validates every chart/trade field", async () => {
  const report = { ...metadata, metrics: { total_return_pct: 1, annualized_return_pct: 2, annualized_vol_pct: 3, sharpe: 0.1, sortino: 0, calmar: null, max_drawdown_pct: 0, win_rate: 50, turnover_rate: 0.2, periods: 10 }, nav: [{ date: "2026-09-11", nav: 1.01 }], trades: [{ date: "2026-09-11", code: "600519.SH", name: "贵州茅台", weight: 0.05, action: "buy", factor_value: null }], rebalance_count: 2, params: {} }
  globalThis.fetch = async () => Response.json(report)
  const result = await api.getStockResearchResult(id, "backtest", "token")
  assert.equal(result.metrics.calmar, null)
  assert.equal(result.nav[0].nav, 1.01)
  globalThis.fetch = async () => Response.json({ ...report, nav: [{ date: "2026-09-11", nav: null }] })
  await assert.rejects(api.getStockResearchResult(id, "backtest", "token"))
})

test("original blank-end draft stays valid for replay after midnight while a fresh submission is rejected", async () => {
  const RealDate = Date
  let now = RealDate.parse("2026-10-18T15:59:00Z")
  globalThis.Date = class extends RealDate { constructor(...args) { super(...(args.length ? args : [now])) } static now() { return now } }
  try {
    const start_date = new RealDate(RealDate.parse("2026-10-18T00:00:00Z") - 3660 * 86400000).toISOString().slice(0, 10)
    const original = { ...draft, parameters: { ...draft.parameters, start_date, end_date: "" } }
    const persisted = api.newResearchCreateSchema.parse(original)
    now = RealDate.parse("2026-10-18T16:01:00Z")
    assert.equal(api.newResearchCreateSchema.safeParse(original).success, false)
    assert.deepEqual(api.researchCreateSchema.parse(persisted), persisted)
    let submitted
    globalThis.fetch = async (_url, options) => { submitted = JSON.parse(options.body); return Response.json(job, { status: 202 }) }
    await api.createStockResearchJob(persisted, "bound-token")
    assert.deepEqual(submitted, persisted)
    assert.equal(submitted.parameters.end_date, "")
    assert.equal(submitted.idempotency_key, id)
  } finally { globalThis.Date = RealDate }
})

test("saved factor policy validates counts while previous reports preserve absent policy", async () => {
  const policy = { factor_id: "pb", policy_version: "stock-factor-domain-v1", eligibility_rule: "finite_strictly_positive", missing_value_policy: "exclude_without_imputation", exclusion_rule: "非正和非有限不填补", count_unit: "date_security_observations", start_date: "2024-01-01", end_date: "2024-03-15", counts: { observations: 170, raw_eligible: 120, excluded_nonpositive: 20, excluded_nonfinite: 20, missing: 10, postprocess_eligible: 120, imputed: 0 } }
  globalThis.fetch = async () => Response.json({ ...analysisReport, factor_policies: [policy] })
  const result = await api.getStockResearchResult(id, "analysis", "token")
  assert.deepEqual(result.factor_policies, [policy])
  globalThis.fetch = async () => Response.json({ ...analysisReport, factor_policies: [{ ...policy, counts: { ...policy.counts, excluded_nonpositive: -1 } }] })
  await assert.rejects(api.getStockResearchResult(id, "analysis", "token"))
  globalThis.fetch = async () => Response.json(analysisReport)
  assert.equal((await api.getStockResearchResult(id, "analysis", "token")).factor_policies, undefined)
})

test("report actions stay unavailable on older backend availability responses", async () => {
  const availability = { enabled: true, accepting: false, max_active: 8, max_user_active: 2, max_runtime_seconds: 300, reason: null }
  globalThis.fetch = async () => Response.json(availability)
  assert.equal((await api.getStockResearchAvailability("token")).saved_report_artifacts, false)
  globalThis.fetch = async () => Response.json({ ...availability, saved_report_artifacts: true })
  assert.equal((await api.getStockResearchAvailability("token")).saved_report_artifacts, true)
})

test("stock task reads and cancellation reject a structurally valid different task", async () => {
  globalThis.fetch = async () => Response.json({ ...job, id: "22222222-2222-4222-8222-222222222222" })
  await assert.rejects(api.getStockResearchJob(id, "token"), /不匹配/)
  await assert.rejects(api.cancelStockResearchJob(id, "token"), /不匹配/)
})

test("history rejects duplicate oversized wrong-filter and inconsistent pages", async () => {
  const valid = { items: [job], total: 1, has_more: false }
  for (const malformed of [
    { ...valid, items: [job, job], total: 2 },
    { ...valid, items: [{ ...job, kind: "backtest" }] },
    { ...valid, items: [{ ...job, status: "failed" }] },
    { ...valid, total: 0 },
    { ...valid, has_more: true },
    { ...valid, total: 2, has_more: false },
    { ...valid, items: [job, { ...job, id: "22222222-2222-4222-8222-222222222222" }], total: 2 },
  ]) {
    globalThis.fetch = async () => Response.json(malformed)
    await assert.rejects(api.listStockResearchJobs({ limit: 1, kind: "analysis", status: "pending" }, "token"), /分页|筛选/)
  }
})

test("history accepts valid status query and an empty page beyond a reduced total", async () => {
  let captured
  globalThis.fetch = async (url) => { captured = url; return Response.json({ items: [], total: 1, has_more: false }) }
  assert.equal((await api.listStockResearchJobs({ offset: 20, status: "cancelled", kind: "analysis" }, "token")).total, 1)
  assert.match(captured, /status=cancelled/)
  globalThis.fetch = async () => Response.json({ items: [job], total: 2, has_more: true })
  assert.equal((await api.listStockResearchJobs({ limit: 1, status: "pending" }, "token")).has_more, true)
})

test("a positive total with an empty nonfinal page is not presented as no reports", async () => {
  globalThis.fetch = async () => Response.json({ items: [], total: 23, has_more: true })
  await assert.rejects(api.listStockResearchJobs({ limit: 20 }, "token"), /分页/)
})

test("a short nonfinal page cannot skip unseen jobs when the UI advances by its page limit", async () => {
  globalThis.fetch = async () => Response.json({ items: [job], total: 23, has_more: true })
  await assert.rejects(api.listStockResearchJobs({ limit: 20 }, "token"), /分页/)
})

test("bookmarks capability treats only an old backend 404 as unavailable", async () => {
  globalThis.fetch = async () => Response.json({ detail: "Not Found" }, { status: 404 })
  assert.equal((await api.getStockReportBookmarkAvailability("token")).enabled, false)
  globalThis.fetch = async () => Response.json({ detail: "offline" }, { status: 503 })
  await assert.rejects(api.getStockReportBookmarkAvailability("token"), /offline/)
})

test("bookmark desired state uses strict bool PUT and validates identity state and timestamp", async () => {
  const view = { job_id: id, market: "stock", bookmarked: true, created_at: "2026-10-22T00:00:00Z" }
  const calls = []
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return Response.json(view) }
  assert.equal((await api.setStockReportBookmark(id, true, "bound-token")).bookmarked, true)
  assert.equal(calls[0].options.method, "PUT")
  assert.equal(calls[0].options.headers.Authorization, "Bearer bound-token")
  assert.deepEqual(JSON.parse(calls[0].options.body), { bookmarked: true })
  await assert.rejects(api.setStockReportBookmark(id, "false", "bound-token"))
  assert.equal(calls.length, 1)
  for (const bad of [{ ...view, job_id: "22222222-2222-4222-8222-222222222222" }, { ...view, market: "futures" }, { ...view, created_at: null }, { ...view, bookmarked: false }, { ...view, created_at: "tomorrow" }]) {
    globalThis.fetch = async () => Response.json(bad)
    await assert.rejects(api.getStockReportBookmark(id, "token"))
  }
})

test("bookmark lists preserve history contract and reject uncompleted reports", async () => {
  const completed = { ...job, status: "completed", has_result: true, data_version: "saved-version", finished_at: "2026-10-22T00:00:00Z" }
  let url
  globalThis.fetch = async (path) => { url = path; return Response.json({ items: [completed], total: 1, has_more: false }) }
  assert.equal((await api.listBookmarkedStockResearchJobs({ status: "completed" }, "token")).items[0].id, id)
  assert.match(url, /\/bookmarks\?/)
  globalThis.fetch = async () => Response.json({ items: [job], total: 1, has_more: false })
  await assert.rejects(api.listBookmarkedStockResearchJobs({}, "token"))
})
