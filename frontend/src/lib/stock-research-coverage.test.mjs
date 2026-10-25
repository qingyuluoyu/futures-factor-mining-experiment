import assert from "node:assert/strict"
import test from "node:test"
import { existsSync } from "node:fs"

const coverage = {
  coverage_version: "stock-research-coverage-v1", market: "stock", frequency: "daily", adjustment: "forward_adjusted_unversioned", timezone: "Asia/Shanghai", catalog_count: 5558,
  available: { first_trade_date: "2026-01-01", last_trade_date: "2026-09-30", securities: 300, price_rows: 10000, observed_dates: 100 },
  recommended: { start_date: "2026-07-01", end_date: "2026-09-30" }, requested: { start_date: "2026-07-01", end_date: "2026-09-30" },
  selected: { securities: 300, observed_dates: 61, price_rows: 18000, missing_price_cells: 300, valuation_rows: 18000, financial_rows: 100, panel_cells: 164700 },
  limits: { max_panel_cells: 2000000, max_price_rows: 1000000, max_valuation_rows: 1000000, max_financial_rows: 100000, max_instruments: 10000 }, status: "within_limits", warnings: ["仅统计覆盖"], as_of: "2026-10-18T01:00:00+08:00",
}
const originalFetch = globalThis.fetch
test.afterEach(() => { globalThis.fetch = originalFetch })

test("coverage client exists and validates the complete strict contract", async () => {
  assert.equal(existsSync(new URL("./stock-research-coverage.ts", import.meta.url)), true)
  const api = await import("./stock-research-coverage.ts")
  assert.deepEqual(api.researchCoverageSchema.parse(coverage), coverage)
  for (const invalid of [{ ...coverage, market: "futures" }, { ...coverage, selected: { ...coverage.selected, price_rows: -1 } }, { ...coverage, extra: true }, { ...coverage, available: { ...coverage.available, first_trade_date: "2026-02-30" } }, { ...coverage, limits: { max_panel_cells: 2 } }]) assert.equal(api.researchCoverageSchema.safeParse(invalid).success, false)
})

test("coverage uses its captured token, bounded timeout, no-store and caller cancellation", async () => {
  const api = await import("./stock-research-coverage.ts")
  const controller = new AbortController()
  let request
  const timeout = AbortSignal.timeout
  const durations = []
  AbortSignal.timeout = (duration) => { durations.push(duration); return timeout(duration) }
  try {
    globalThis.fetch = async (url, options) => { request = { url, options }; return Response.json(coverage) }
    await api.getStockResearchCoverage({ start_date: "2026-07-01", end_date: "" }, "captured-token", controller.signal)
    assert.match(request.url, /\/api\/stock\/research\/coverage\?start_date=2026-07-01$/)
    assert.equal(request.options.headers.Authorization, "Bearer captured-token")
    assert.equal(request.options.cache, "no-store")
    assert.deepEqual(durations, [20000])
    controller.abort()
    assert.equal(request.options.signal.aborted, true)
  } finally { AbortSignal.timeout = timeout }
})

test("coverage errors preserve status without retries or false empty data", async () => {
  const api = await import("./stock-research-coverage.ts")
  let calls = 0
  globalThis.fetch = async () => { calls++; return Response.json({ detail: "覆盖查询超时" }, { status: 503 }) }
  await assert.rejects(api.getStockResearchCoverage({}, "token"), (error) => error.status === 503 && /覆盖查询超时/.test(error.message))
  assert.equal(calls, 1)
})

test("selected dependencies use v2 with repeated factors and verified schema, never legacy fallback", async () => {
  const api = await import("./stock-research-coverage.ts")
  const dependencies = { version: "stock-research-dependencies-v1", selection: "factor_dependencies", price_fields: ["close"], valuation_fields: ["total_mv"], financial_fields: [] }
  const result = { ...coverage, coverage_version: "stock-research-coverage-v2", dependencies }
  let request
  globalThis.fetch = async (url, options) => { request = { url, options }; return Response.json(result) }
  assert.deepEqual(await api.getStockResearchCoverage({ start_date: "2026-07-01", end_date: "2026-09-30", factor_ids: ["ret_20d", "vol_20d"], mktcap_neutral: true, weight: "equal" }, "token"), result)
  const url = new URL(request.url, "http://test")
  assert.equal(url.pathname, "/api/stock/research/coverage-v2")
  assert.deepEqual(url.searchParams.getAll("factor_ids"), ["ret_20d", "vol_20d"])
  assert.equal(url.searchParams.get("mktcap_neutral"), "true")
  globalThis.fetch = async () => Response.json(coverage)
  await assert.rejects(api.getStockResearchCoverage({ factor_ids: ["ret_20d"] }, "token"))
  assert.equal(api.researchCoverageSchema.safeParse({ ...result, dependencies: { ...dependencies, valuation_fields: ["unknown"] } }).success, false)
})
