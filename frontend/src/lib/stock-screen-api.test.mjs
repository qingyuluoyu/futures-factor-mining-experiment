import assert from "node:assert/strict"
import test from "node:test"

const metadata = { screen_fingerprint: "a".repeat(64), as_of: "2026-10-19", previous_date: "2026-10-16", classification: "current", source: "local-fixture", price_adjustment: "none", volume_unit: "lot_100_shares", method_version: "fixture", coverage: { instruments: 2, quoted: 1, comparable: 1 }, items: [], total: 0, has_more: false }

test("stock screen uses normalized typed conditions and verifies applied filters", async () => {
  const api = await import("./stock-screen-api.ts")
  const filters = api.parseStockScreenDraft({ keyword: "银行", min_close: "10", max_close: "20", min_change_pct: "0", min_volume: "0" }, "银行")
  let observed
  globalThis.fetch = async (url) => { observed = new URL(url, "http://fixture"); return Response.json({ ...metadata, screen_version: "stock-screen-v1", applied_filters: filters, filter_units: { price: "CNY_per_share", change_pct: "percent", volume: "lot_100_shares" } }) }
  const result = await api.getStockScreenApi(filters)
  assert.equal(observed.pathname, "/api/stock/market/screen")
  assert.equal(observed.searchParams.get("min_change_pct"), "0")
  assert.equal(observed.searchParams.get("min_volume"), "0")
  assert.equal(result.applied_filters.keyword, "银行")
  for (const response of [{ ...metadata }, { ...metadata, screen_version: "stock-screen-v1", applied_filters: { ...filters, min_close: null }, filter_units: { price: "CNY_per_share", change_pct: "percent", volume: "lot_100_shares" } }]) {
    globalThis.fetch = async () => Response.json(response)
    await assert.rejects(api.getStockScreenApi(filters), /筛选响应|应用条件/)
  }
})

test("invalid stock screen numbers and reversed ranges never request data", async () => {
  const api = await import("./stock-screen-api.ts")
  let requests = 0
  globalThis.fetch = async () => { requests++; return Response.json({}) }
  for (const draft of [{ min_close: "12", max_close: "11" }, { min_volume: "-1" }, { min_close: "nan" }, { min_close: "0x10" }, { min_change_pct: "inf" }]) {
    assert.throws(() => api.parseStockScreenDraft(draft), /筛选|数字|下限/)
  }
  await assert.rejects(api.getStockScreenApi({ min_close: Number.NaN }), /筛选/)
  assert.equal(requests, 0)
})
