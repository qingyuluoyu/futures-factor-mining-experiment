import assert from "node:assert/strict"
import test from "node:test"

const accountId = "10000000-0000-4000-8000-000000000001"
const fixture = {
  history_version: "stock-replay-order-history-v1", market: "stock", account_id: accountId, trading_mode: "virtual", revision: 210, current_date: "2026-09-03", method_version: "stock-replay-v1",
  query: { expected_revision: 210, status: "all", direction: "all", code: null, start: null, end: null, limit: 20, offset: 0 }, total: 1,
  items: [{ id: "20000000-0000-4000-8000-000000000001", code: "000001", instrument_id: "000001.SZ", direction: "buy", quantity: 100, order_type: "market", limit_price: null, status: "filled", reason: null, submitted_on: "2026-09-01", filled_on: "2026-09-02", filled_price: "10.0000", fees: "5.01", cash_delta: "-1005.01", realized_pnl: "0.00", created_at: "2026-10-21T01:00:00.000000+00:00" }], has_more: false,
  dataset_bindings: [{ code: "000001", dataset_id: "30000000-0000-4000-8000-000000000001" }], source: "internal_day_replay", supplier: "internal_simulator", currency: "CNY", quantity_unit: "share", price_unit: "CNY_per_share", amount_unit: "CNY", timezone: "Asia/Shanghai", frequency: "order_event", price_adjustment: "none", license_scope: "account_owner_private", trading_calendar: "account_bound_snapshot_not_revalidated_here", financial_basis: "not_applicable", collected_at: null, data_version: null, point_in_time_certified: false, read_at: "2026-10-21T01:00:00Z", source_calls: 0, model_calls: 0, transaction: "postgresql_repeatable_read_readonly", limitations: ["不是券商实盘账单或不可变历史版本"] }

test("history validates exact decimal strings, fill state, cursor, filter and page identities", async () => {
  const { replayOrderHistorySchema } = await import("./stock-replay-history-api.ts")
  assert.deepEqual(replayOrderHistorySchema.parse(fixture), fixture)
  for (const change of [{ total: 2 }, { has_more: true }, { revision: 211 }, { dataset_bindings: [] }, { point_in_time_certified: true },
    { items: [{ ...fixture.items[0], cash_delta: "1e300" }] }, { items: [{ ...fixture.items[0], fees: "-1.00" }] },
    { items: [{ ...fixture.items[0], fees: "9".repeat(100) + ".00" }] }, { items: [{ ...fixture.items[0], filled_on: "2026-09-04" }] },
    { items: [{ ...fixture.items[0], status: "pending" }] }, { items: [{ ...fixture.items[0], instrument_id: "000001.SH" }] },
    { query: { ...fixture.query, direction: "sell" } }])
    assert.equal(replayOrderHistorySchema.safeParse({ ...fixture, ...change }).success, false)
})

test("history binds account mode token and query, supports cancellation and no automatic retries", async () => {
  const { getStockReplayOrderHistory } = await import("./stock-replay-history-api.ts")
  let calls = 0, request
  globalThis.fetch = async (url, options) => { calls++; request = { url, options }; return Response.json(fixture) }
  const controller = new AbortController()
  assert.deepEqual(await getStockReplayOrderHistory(accountId, { expected_revision: 210 }, "captured-token", "virtual", controller.signal), fixture)
  assert.match(request.url, /\/accounts\/10000000-0000-4000-8000-000000000001\/order-history\?/)
  assert.equal(request.options.headers.Authorization, "Bearer captured-token")
  assert.equal(request.options.cache, "no-store")
  controller.abort(); assert.equal(request.options.signal.aborted, true)
  await assert.rejects(getStockReplayOrderHistory(accountId, { expected_revision: 210 }, "token", "virtual", controller.signal)); assert.equal(calls, 1)
  globalThis.fetch = async () => Response.json({ ...fixture, trading_mode: "live" })
  await assert.rejects(getStockReplayOrderHistory(accountId, { expected_revision: 210 }, "token", "virtual"), /回显/)
  globalThis.fetch = async () => Response.json({ ...fixture, account_id: "10000000-0000-4000-8000-000000000002" })
  await assert.rejects(getStockReplayOrderHistory(accountId, { expected_revision: 210 }, "token", "virtual"), /回显/)
})

test("revision conflicts require account refresh and failures are never replaced by recent200 or empty", async () => {
  const { getStockReplayOrderHistory } = await import("./stock-replay-history-api.ts")
  globalThis.fetch = async () => Response.json({ detail: "不展示的异常正文" }, { status: 409, headers: { "X-Replay-History-Error": "history_revision_conflict", "X-Request-ID": "a".repeat(32) } })
  await assert.rejects(getStockReplayOrderHistory(accountId, { expected_revision: 210 }, "token", "virtual"), (error) => error.requires_refresh === true && /刷新账户/.test(error.message) && !error.message.includes("异常正文"))
  globalThis.fetch = async () => Response.json({}, { status: 503 })
  await assert.rejects(getStockReplayOrderHistory(accountId, { expected_revision: 210 }, "token", "virtual"), /503/)
})
