import assert from "node:assert/strict"
import test from "node:test"

const fixture = {
  history_version: "stock-fundamentals-history-v1", market: "stock", query: { code: "600001", kind: "financial", as_of: "2024-01-03", limit: 20, offset: 0 },
  instrument: { code: "600001", name: "证券", exchange: "SSE", instrument_id: "600001.SSE" },
  counts: { source_rows: 4, eligible_rows: 1, unknown_publication: 1, invalid_dates: 1, later_rows: 1 },
  rows: [{ report_date: "2023-09-30", publish_date: "2024-01-03", roe: -12, gross_margin: null, revenue_yoy: 2, netprofit_yoy: -3, eps: -1, bps: 2, netprofit: -100 }], has_more: false,
  units: { roe: "percent", gross_margin: "percent", revenue_yoy: "percent", netprofit_yoy: "percent", eps: "CNY_per_share", bps: "CNY_per_share", netprofit: "CNY" },
  source: "local_import", provenance: "legacy_unverified", data_version: null, point_in_time_certified: false, currency: "CNY", timezone: "Asia/Shanghai", transaction: "postgresql_repeatable_read_readonly",
  read_at: "2026-10-20T00:00:00Z", source_calls: 0, model_calls: 0, limitations: ["不能证明PIT时点"] }
Object.assign(fixture, { supplier: "unknown", license_scope: "unverified", collected_at: null, trading_calendar: "unverified", frequency: "quarterly", financial_basis: "unverified", price_adjustment: "not_applicable", unit_status: "schema_declared_not_row_verified", date_semantics: { report_date: "report_period_end", publish_date: "disclosure_date_inclusive_display_only" } })

test("fundamental history validates classification partitions, dates, pagination and security identity", async () => {
  const { stockFundamentalsSchema } = await import("./stock-fundamentals-api.ts")
  assert.deepEqual(stockFundamentalsSchema.parse(fixture), fixture)
  for (const change of [{ counts: { ...fixture.counts, source_rows: 5 } }, { rows: [] }, { has_more: true },
    { instrument: { ...fixture.instrument, instrument_id: "600001.SZSE" } }, { point_in_time_certified: true },
    { rows: [{ ...fixture.rows[0], publish_date: "2024-01-04" }] }, { units: { ...fixture.units, eps: "percent" } },
    { rows: [{ ...fixture.rows[0], roe: Infinity }] }, { query: { ...fixture.query, kind: "valuation" } },
    { rows: [fixture.rows[0], fixture.rows[0]], counts: { ...fixture.counts, eligible_rows: 2, source_rows: 5 } }])
    assert.equal(stockFundamentalsSchema.safeParse({ ...fixture, ...change }).success, false)
})

test("valuation history retains negative and null values but rejects unsorted or future days", async () => {
  const { stockFundamentalsSchema } = await import("./stock-fundamentals-api.ts")
  const value = { ...fixture, query: { ...fixture.query, kind: "valuation" }, counts: { source_rows: 2, eligible_rows: 2, unknown_publication: 0, invalid_dates: 0, later_rows: 0 },
    rows: [{ trade_date: "2024-01-03", pe_ttm: -10, pb: null, total_mv: 100, circ_mv: 50, turnover_rate: 1 }, { trade_date: "2024-01-02", pe_ttm: -11, pb: 2, total_mv: 99, circ_mv: 49, turnover_rate: 2 }],
    units: { pe_ttm: "ratio", pb: "ratio", total_mv: "CNY_100_million", circ_mv: "CNY_100_million", turnover_rate: "percent" }, frequency: "daily", date_semantics: { trade_date: "trading_date" } }
  assert.equal(stockFundamentalsSchema.safeParse(value).success, true)
  assert.equal(stockFundamentalsSchema.safeParse({ ...value, rows: [...value.rows].reverse() }).success, false)
  assert.equal(stockFundamentalsSchema.safeParse({ ...value, rows: [{ ...value.rows[0], trade_date: "2024-01-04" }, value.rows[1]] }).success, false)
})

test("history request captures token and cancellation, rejects mismatched echo and never retries", async () => {
  const { getStockFundamentals } = await import("./stock-fundamentals-api.ts")
  let calls = 0, request
  globalThis.fetch = async (url, options) => { calls++; request = { url, options }; return Response.json(fixture) }
  const controller = new AbortController()
  assert.deepEqual(await getStockFundamentals(fixture.query, "captured-token", controller.signal), fixture)
  assert.match(request.url, /\/api\/stock\/market\/fundamentals\?/)
  assert.equal(request.options.headers.Authorization, "Bearer captured-token")
  assert.equal(request.options.cache, "no-store")
  controller.abort(); assert.equal(request.options.signal.aborted, true)
  await assert.rejects(getStockFundamentals(fixture.query, "token", controller.signal))
  assert.equal(calls, 1)
  globalThis.fetch = async () => Response.json({ ...fixture, query: { ...fixture.query, code: "600002" }, instrument: { ...fixture.instrument, code: "600002", instrument_id: "600002.SSE" } })
  await assert.rejects(getStockFundamentals(fixture.query, "token"), /回显/)
})

test("source failures or old deployments do not become a valid empty history", async () => {
  const { getStockFundamentals } = await import("./stock-fundamentals-api.ts")
  globalThis.fetch = async () => Response.json({ detail: "敏感后端正文" }, { status: 503, headers: { "X-Request-ID": "b".repeat(32) } })
  await assert.rejects(getStockFundamentals(fixture.query, "token"), (error) => /503/.test(error.message) && error.request_id === "b".repeat(32) && !error.message.includes("敏感"))
  globalThis.fetch = async () => new Response("", { status: 404 })
  await assert.rejects(getStockFundamentals(fixture.query, "token"), /证券未收录或历史接口尚未部署/)
  await assert.rejects(getStockFundamentals({ ...fixture.query, code: "６００００１" }, "token"))
})
