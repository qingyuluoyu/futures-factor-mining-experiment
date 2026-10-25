import assert from "node:assert/strict"
import test from "node:test"

const api = await import("./stock-archive-observation-api.ts")
const version = "a".repeat(64), instrument = "600001.SH"
const payload = { manifest_version: "stock-raw-panel-v1", versions: [version], instrument_ids: [instrument],
  financial_metadata: { market: "CN_A", currency: "CNY", timezone: "Asia/Shanghai", frequency: "daily", price_adjustment: "none", volume_unit: "lot_100_shares", amount_unit: "CNY", provider: "user_authorized_teajoin_tushare_compatible", source_uri: "https://teajoin.com/debug/api/daily", reference_price: "vendor_ex_rights_pre_close", license_scope: "operator_configured_account_not_redistribution_certified" },
  batches: [{ version, source_data_version: "b".repeat(64), source_batch_id: "11111111-1111-4111-8111-111111111111", archive_batch_id: "22222222-2222-4222-8222-222222222222", trade_date: "2026-10-21", method_version: "stock-daily-archive-2026-10-19.v1", source_collected_at: "2026-10-21T09:00:00Z", archived_at: "2026-10-21T10:00:00Z", verified_row_count: 2, expected_count: 3, missing_count: 1, unknown_count: 0 }],
  observations: [{ instrument_id: instrument, trade_date: "2026-10-21", archive_version: version, open: "12.000000000000000001", close: "12.000000000000000002", high: "12.000000000000000003", low: "12", pre_close: "11.9", volume: "0.01", amount: "456789" }], missing: [],
  verification: "stored_source_and_archive_content_equal_not_independently_certified", number_encoding: "decimal_of_stored_json_number_not_original_response_text", data_available_at: null, point_in_time_verified: false, eligible_for_backtest: false, usage: "internal_raw_observation_audit_not_backtest_dataset", calendar_continuity_verified: false, source_calls: 0, model_calls: 0, content_sha256: "c".repeat(64), generated_at: "2026-10-22T00:00:00Z" }

test("pinned raw observation uses authenticated no-store GET and preserves decimal strings", async () => {
  let seen
  globalThis.fetch = async (url, options) => { seen = { url: new URL(url, "http://localhost"), options }; return Response.json(payload) }
  const result = await api.getStockArchiveObservation(version, instrument, "fixture-token")
  assert.equal(result.observations[0].volume, "0.01")
  assert.equal(result.observations[0].close, "12.000000000000000002")
  assert.equal(seen.url.pathname, `/api/stock/market/archive/versions/${version}/observation`)
  assert.equal(seen.url.searchParams.get("instrument_id"), instrument)
  assert.equal(seen.options.cache, "no-store")
  assert.equal(seen.options.headers.Authorization, "Bearer fixture-token")
})

test("mismatched version identity, missing partitions, false certification and exact OHLC conflict fail", async () => {
  for (const changed of [{ ...payload, versions: ["d".repeat(64)] }, { ...payload, observations: [] }, { ...payload, point_in_time_verified: true }, { ...payload, observations: [{ ...payload.observations[0], instrument_id: "000001.SZ" }] }, { ...payload, observations: [{ ...payload.observations[0], high: "12.000000000000000001" }] }, { ...payload, observations: [{ ...payload.observations[0], volume: 0 }] }, { ...payload, source_evidence: "must-not-expose" }]) {
    globalThis.fetch = async () => Response.json(changed)
    await assert.rejects(() => api.getStockArchiveObservation(version, instrument, "fixture-token"))
  }
})

test("explicit source missing reason is not converted into zero prices or a suspension claim", async () => {
  globalThis.fetch = async () => Response.json({ ...payload, observations: [], missing: [{ instrument_id: instrument, trade_date: "2026-10-21", archive_version: version, reason: "source_missing" }] })
  const result = await api.getStockArchiveObservation(version, instrument, "fixture-token")
  assert.equal(result.observations.length, 0)
  assert.equal(result.missing[0].reason, "source_missing")
})

test("invalid request identity fails before network and errors are not retried or empty success", async () => {
  let calls = 0
  globalThis.fetch = async () => { calls++; return Response.json({ detail: "固定版本不存在" }, { status: 404, headers: { "X-Request-ID": "fixture-request" } }) }
  for (const [hash, identifier] of [["latest", instrument], [version, "600001"], [version, "600001.SZ"], [version, "000001.SH"]]) await assert.rejects(() => api.getStockArchiveObservation(hash, identifier, "fixture-token"))
  assert.equal(calls, 0)
  await assert.rejects(() => api.getStockArchiveObservation(version, instrument, "fixture-token"), /不存在.*fixture-request/)
  assert.equal(calls, 1)
})
