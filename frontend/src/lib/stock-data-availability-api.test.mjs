import assert from "node:assert/strict"
import test from "node:test"

const coverage = {
  coverage_version: "stock-research-coverage-v1", market: "stock", frequency: "daily", adjustment: "forward_adjusted_unversioned", timezone: "Asia/Shanghai", catalog_count: 3,
  available: { first_trade_date: "2026-07-01", last_trade_date: "2026-10-19", securities: 2, price_rows: 100, observed_dates: 60 },
  recommended: { start_date: "2026-07-01", end_date: "2026-10-19" }, requested: { start_date: "2026-07-01", end_date: "2026-10-19" },
  selected: { securities: 2, observed_dates: 60, price_rows: 100, missing_price_cells: 20, valuation_rows: 0, financial_rows: 0, panel_cells: 1200 },
  limits: { max_panel_cells: 2000000, max_price_rows: 1000000, max_valuation_rows: 1000000, max_financial_rows: 1000000, max_instruments: 10000 }, status: "within_limits", warnings: ["不能证明连续历史"], as_of: "2026-10-20T10:00:00Z",
}
const boards = { as_of: "2026-10-19", previous_date: "2026-10-16", classification: "current", source: "fixture", price_adjustment: "none", volume_unit: "lot_100_shares", method_version: "fixture", coverage: { instruments: 3, quoted: 2, comparable: 1 }, boards: [], total: 0, has_more: false }
const payloads = { "/api/stock/market/boards": boards, "/api/stock/research/coverage": coverage, "/api/stock/replay/availability": { enabled: false, datasets: [] }, "/api/stock/research/availability": { enabled: true, accepting: false, max_active: 2, max_user_active: 1, max_runtime_seconds: 300, reason: "执行器未就绪" } }

test("coverage reads use one captured credential, bounded signals and no-store without any write", async () => {
  const { getStockDataAvailability } = await import("./stock-data-availability-api.ts")
  const original = globalThis.fetch, calls = [], controller = new AbortController()
  globalThis.fetch = async (url, options) => { calls.push({ url: new URL(url, "http://fixture"), options }); return Response.json(payloads[new URL(url, "http://fixture").pathname]) }
  try {
    const result = await getStockDataAvailability({}, "captured-token", controller.signal)
    assert.equal(calls.length, 4)
    assert.ok(calls.every(({ options }) => options.headers.Authorization === "Bearer captured-token" && options.method === "GET" && options.cache === "no-store" && options.signal))
    assert.equal(result.market.state, "ready"); assert.equal(result.coverage.data.selected.missing_price_cells, 20)
    assert.equal(result.replay.data.enabled, false); assert.equal(result.research.data.accepting, false)
    controller.abort(); assert.ok(calls.every(({ options }) => options.signal.aborted))
  } finally { globalThis.fetch = original }
})

test("partial HTTP or schema failures stay distinct from zero coverage and never retry", async () => {
  const { getStockDataAvailability } = await import("./stock-data-availability-api.ts")
  const original = globalThis.fetch, calls = []
  globalThis.fetch = async (url) => {
    const path = new URL(url, "http://fixture").pathname; calls.push(path)
    if (path.endsWith("/boards")) return Response.json({ detail: "private-db-fixture" }, { status: 503, headers: { "X-Request-ID": "fixture-trace" } })
    if (path === "/api/stock/replay/availability") return Response.json({ enabled: true, datasets: null })
    return Response.json(payloads[path])
  }
  try {
    const result = await getStockDataAvailability({}, "captured-token")
    assert.equal(calls.length, 4)
    assert.equal(result.market.state, "failed"); assert.equal(result.replay.state, "failed")
    assert.match(result.market.message, /503/); assert.doesNotMatch(result.market.message, /private-db-fixture/)
    assert.equal(result.market.request_id, "fixture-trace"); assert.equal(result.coverage.state, "ready")
  } finally { globalThis.fetch = original }
})

test("period validation rejects malformed future inverted or oversized windows before network", async () => {
  const { parseAvailabilityPeriod, getStockDataAvailability } = await import("./stock-data-availability-api.ts")
  assert.deepEqual(parseAvailabilityPeriod({ start_date: "", end_date: "" }, "2026-10-20"), {})
  assert.deepEqual(parseAvailabilityPeriod({ start_date: "2026-10-01", end_date: "2026-10-19" }, "2026-10-20"), { start_date: "2026-10-01", end_date: "2026-10-19" })
  for (const period of [{ start_date: "2026-02-30" }, { start_date: "2026-10-19", end_date: "2026-10-01" }, { end_date: "2099-01-01" }, { start_date: "2010-01-01", end_date: "2026-10-19" }]) assert.throws(() => parseAvailabilityPeriod(period, "2026-10-20"))
  const original = globalThis.fetch; let calls = 0
  globalThis.fetch = async () => { calls++; throw new Error("must-not-fetch") }
  try { await assert.rejects(getStockDataAvailability({ start_date: "2026-10-19", end_date: "2026-10-01" }, "captured-token")); assert.equal(calls, 0) } finally { globalThis.fetch = original }
})

test("already cancelled requests fail as cancellation rather than fake partial-empty success", async () => {
  const { getStockDataAvailability } = await import("./stock-data-availability-api.ts")
  const controller = new AbortController(); controller.abort()
  await assert.rejects(getStockDataAvailability({}, "captured-token", controller.signal))
})

test("impossible market coverage counts and disabled replay datasets are rejected independently", async () => {
  const { getStockDataAvailability } = await import("./stock-data-availability-api.ts")
  const original = globalThis.fetch
  globalThis.fetch = async (url) => {
    const path = new URL(url, "http://fixture").pathname
    if (path.endsWith("/boards")) return Response.json({ ...boards, coverage: { instruments: 3, quoted: 4, comparable: 1 } })
    if (path === "/api/stock/replay/availability") return Response.json({ enabled: false, datasets: [{ id: "fixture", code: "600001", instrument_id: "600001.SH", name: "fixture", start_date: "2026-10-19", end_date: "2026-10-01", version: "fixture", source: "fixture", adjustment: "none" }] })
    return Response.json(payloads[path])
  }
  try {
    const result = await getStockDataAvailability({}, "captured-token")
    assert.equal(result.market.state, "failed"); assert.equal(result.replay.state, "failed")
    assert.equal(result.coverage.state, "ready"); assert.equal(result.research.state, "ready")
  } finally { globalThis.fetch = original }
})
