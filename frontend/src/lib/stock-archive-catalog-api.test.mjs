import assert from "node:assert/strict"
import test from "node:test"

const api = await import("./stock-archive-catalog-api.ts")
const item = { batch_id: "11111111-1111-4111-8111-111111111111", version: "a".repeat(64), source_data_version: "b".repeat(64), catalog_version: "c".repeat(64), method_version: "stock-daily-archive-2026-10-19.v1", trade_date: "2026-10-21", source_collected_at: "2026-10-21T09:00:00Z", archived_at: "2026-10-21T10:00:00Z", row_count: 5548, expected_count: 5558, missing_count: 10, unknown_count: 20, source_row_count: 5568,
  market: "CN_A", currency: "CNY", timezone: "Asia/Shanghai", frequency: "daily", price_adjustment: "none", volume_unit: "lot_100_shares", amount_unit: "CNY", provider: "user_authorized_teajoin_tushare_compatible", source_uri: "https://teajoin.com/debug/api/daily", reference_price: "vendor_ex_rights_pre_close", license_scope: "operator_configured_account_not_redistribution_certified", verification: "metadata_only_not_row_or_PIT_certification", research_ready: false, replay_ready: false }
const page = { contract_version: "stock-archive-catalog-v1", snapshot_at: "2026-10-22T00:00:00Z", membership_token: "d".repeat(32), items: [item], total: 1, has_more: false }

test("catalog preserves snapshot and bounded filters in a single authenticated read", async () => {
  let observed
  globalThis.fetch = async (url, options) => { observed = { url: new URL(url, "http://localhost"), options }; return Response.json(page) }
  const result = await api.getStockArchiveCatalog({ limit: 20, offset: 0, trade_date: "2026-10-21", snapshot_at: page.snapshot_at, membership_token: page.membership_token }, "fixture-token")
  assert.equal(result.items[0].missing_count, 10)
  assert.equal(observed.url.pathname, "/api/stock/market/archive/versions")
  assert.equal(observed.url.searchParams.get("snapshot_at"), page.snapshot_at)
  assert.equal(observed.url.searchParams.get("membership_token"), page.membership_token)
  assert.equal(observed.options.headers.Authorization, "Bearer fixture-token")
  assert.equal(observed.options.cache, "no-store")
})

test("catalog rejects contradictory coverage, certification, time, page and snapshot responses", async () => {
  for (const changed of [{ ...page, items: [{ ...item, missing_count: 0 }] }, { ...page, items: [{ ...item, research_ready: true }] }, { ...page, items: [{ ...item, price_adjustment: "qfq" }] }, { ...page, items: [{ ...item, source_collected_at: "2026-10-21T08:00:00Z" }] }, { ...page, items: [item, item], total: 2 }, { ...page, total: 25, has_more: true }, { ...page, snapshot_at: "2026-10-21T00:00:00Z" }, { ...page, items: [{ ...item, source_evidence: "must-not-return" }] }]) {
    globalThis.fetch = async () => Response.json(changed)
    await assert.rejects(() => api.getStockArchiveCatalog({ limit: 20, offset: 0, snapshot_at: page.snapshot_at, membership_token: page.membership_token }, "fixture-token"))
  }
})

test("invalid queries fail before network and provider failure is not an empty result", async () => {
  let calls = 0
  globalThis.fetch = async () => { calls++; return Response.json({ detail: "归档未就绪" }, { status: 503, headers: { "X-Request-ID": "fixture-request" } }) }
  for (const query of [{ limit: 21 }, { offset: -1 }, { offset: 1 }, { snapshot_at: page.snapshot_at }, { membership_token: page.membership_token }, { trade_date: "2026-02-30" }, { snapshot_at: "2026-10-21T00:00:00" }, { unexpected: true }]) await assert.rejects(() => api.getStockArchiveCatalog(query, "fixture-token"))
  assert.equal(calls, 0)
  await assert.rejects(() => api.getStockArchiveCatalog({}, "fixture-token"), /归档未就绪.*fixture-request/)
  assert.equal(calls, 1)
})

test("malformed timestamp remains a schema failure rather than throwing in financial refinement", () => {
  for (const field of ["source_collected_at", "archived_at"]) {
    const malformed = { ...page, items: [{ ...item, [field]: "not-a-time" }] }
    assert.doesNotThrow(() => { assert.equal(api.stockArchiveCatalogSchema.safeParse(malformed).success, false) })
  }
})

test("changed membership is not reused or retried and mismatched response token fails", async () => {
  let calls = 0
  const query = { offset: 20, snapshot_at: page.snapshot_at, membership_token: page.membership_token }
  globalThis.fetch = async () => { calls++; return Response.json({ detail: "归档目录成员已变化，请显式刷新" }, { status: 409 }) }
  await assert.rejects(() => api.getStockArchiveCatalog(query, "fixture-token"), /已变化.*刷新/)
  assert.equal(calls, 1)
  globalThis.fetch = async () => Response.json({ ...page, items: [], total: 1, membership_token: "e".repeat(32) })
  await assert.rejects(() => api.getStockArchiveCatalog(query, "fixture-token"), /分页|快照|成员/)
})
