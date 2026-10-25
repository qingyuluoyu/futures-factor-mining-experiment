import assert from "node:assert/strict"
import test from "node:test"

const query = { kind: "all", keyword: "", limit: 50, offset: 0, expected_fingerprint: null }
const payload = { gap_version: "stock-eod-gaps-v1", state: "ready", snapshot: { trade_date: "2024-01-03", data_version: "a".repeat(64), provider: "user_authorized_teajoin_tushare_compatible", source_uri: "https://teajoin.com/debug/api/daily", collected_at: "2024-01-03T09:00:00Z", price_adjustment: "none", volume_unit: "lot_100_shares", license_scope: "operator_configured_account_not_redistribution_certified", source_evidence_recorded: false, verification: "stored_metadata_only_not_independent_certification", history_adjustment: "legacy_qfq_unversioned" }, totals: { missing: 1, unknown: 0 }, query_fingerprint: "b".repeat(64), total: 1, has_more: false, applied_query: query, items: [{ code: "600102", kind: "missing", name: "缺口", industry: null, catalog_status: "present_now", instrument_id: "600102.SH", instrument_id_basis: "current_catalog", current_list_date: null, history: { rows: 0, first_date: null, last_date: null } }] }

test("gap client captures credential, pagination query and bounded read-only request", async () => {
  const { getStockEodGaps } = await import("./stock-eod-gaps-api.ts")
  const original = globalThis.fetch; let request
  globalThis.fetch = async (url, options) => { request = { url, options }; return Response.json(payload) }
  try {
    const controller = new AbortController()
    const result = await getStockEodGaps({}, "captured-token", controller.signal)
    assert.equal(result.items[0].history.rows, 0)
    assert.match(request.url, /\/api\/stock\/market\/eod-gaps\?/)
    assert.equal(request.options.headers.Authorization, "Bearer captured-token")
    assert.equal(request.options.cache, "no-store"); assert.equal(request.options.method, "GET")
    controller.abort(); assert.equal(request.options.signal.aborted, true)
  } finally { globalThis.fetch = original }
})

test("invalid conditions never fetch and failures preserve status without provider detail or retries", async () => {
  const { getStockEodGaps } = await import("./stock-eod-gaps-api.ts")
  const original = globalThis.fetch; let calls = 0
  globalThis.fetch = async () => { calls++; return Response.json({ detail: "private-detail-fixture" }, { status: 409, headers: { "X-Stock-Gap-Error": "snapshot_changed" } }) }
  try {
    await assert.rejects(getStockEodGaps({ limit: 101 }, "captured-token")); assert.equal(calls, 0)
    await assert.rejects(getStockEodGaps({ expected_fingerprint: "bad" }, "captured-token")); assert.equal(calls, 0)
    await assert.rejects(getStockEodGaps({}, "captured-token"), (error) => error.status === 409 && /重新应用/.test(error.message) && !/private-detail/.test(error.message))
    assert.equal(calls, 1)
  } finally { globalThis.fetch = original }
})

test("bad counts, dates, history ranges or echoed query reject instead of old or fake data", async () => {
  const { getStockEodGaps } = await import("./stock-eod-gaps-api.ts")
  const original = globalThis.fetch
  try {
    for (const invalid of [{ ...payload, gap_version: "old" }, { ...payload, totals: { missing: 0, unknown: 0 } }, { ...payload, applied_query: { ...query, kind: "unknown" } }, { ...payload, items: [{ ...payload.items[0], history: { rows: 1, first_date: "2024-01-04", last_date: "2024-01-04" } }] }]) {
      globalThis.fetch = async () => Response.json(invalid)
      await assert.rejects(getStockEodGaps({}, "captured-token"))
    }
  } finally { globalThis.fetch = original }
})

test("disabled and absent snapshots remain distinct non-certification states", async () => {
  const { getStockEodGaps } = await import("./stock-eod-gaps-api.ts")
  const original = globalThis.fetch
  try {
    for (const state of ["disabled", "no_snapshot"]) {
      globalThis.fetch = async () => Response.json({ gap_version: "stock-eod-gaps-v1", state, snapshot: null, totals: null, query_fingerprint: null, items: [], total: 0, has_more: false, applied_query: query })
      const result = await getStockEodGaps({}, "captured-token")
      assert.equal(result.state, state); assert.equal(result.totals, null)
    }
  } finally { globalThis.fetch = original }
})

test("category totals cannot contradict returned rows or an unfiltered total", async () => {
  const { getStockEodGaps } = await import("./stock-eod-gaps-api.ts")
  const original = globalThis.fetch
  try {
    for (const totals of [{ missing: 0, unknown: 1 }, { missing: 2, unknown: 0 }]) {
      globalThis.fetch = async () => Response.json({ ...payload, totals })
      await assert.rejects(getStockEodGaps({}, "captured-token"))
    }
  } finally { globalThis.fetch = original }
})

test("a pinned query cannot accept a different fingerprint or a success-shaped source disappearance", async () => {
  const { getStockEodGaps } = await import("./stock-eod-gaps-api.ts")
  const original = globalThis.fetch
  const pinned = { ...query, expected_fingerprint: "a".repeat(64) }
  try {
    for (const response of [{ ...payload, applied_query: pinned }, { gap_version: "stock-eod-gaps-v1", state: "no_snapshot", snapshot: null, totals: null, query_fingerprint: null, items: [], total: 0, has_more: false, applied_query: pinned }]) {
      globalThis.fetch = async () => Response.json(response)
      await assert.rejects(getStockEodGaps(pinned, "captured-token"))
    }
  } finally { globalThis.fetch = original }
})
