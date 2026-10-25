import assert from "node:assert/strict"
import test from "node:test"

globalThis.localStorage = { getItem: () => "qa-token" }
globalThis.window = { location: { href: "" } }
const api = await import("./stock-market-api.ts")
const metadata = {
  as_of: "2026-10-16", previous_date: "2026-10-15", classification: "当前行业",
  source: "本地授权导入库", price_adjustment: "qfq", volume_unit: "lot_100_shares",
  method_version: "stock-market-snapshot-v1", coverage: { instruments: 5558, quoted: 300, comparable: 300 },
}
test("members use bounded server pagination and encode filters without losing null quotes", async () => {
  let observed
  globalThis.fetch = async (input) => {
    observed = new URL(input, "http://localhost")
    return new Response(JSON.stringify({ ...metadata, total: 1, has_more: false, items: [{
      code: "600519", instrument_id: "600519.SH", name: "贵州茅台", exchange: "SSE", industry: "白酒",
      close: null, change: null, change_pct: null, volume: null, trade_date: null,
      data_available: false, data_status: "no_daily",
    }] }))
  }
  const result = await api.getStockMembersApi({ industry: "白酒", keyword: "茅台", offset: 50, dataOnly: true, sort: "change_pct", direction: "desc" })
  assert.equal(observed.pathname, "/api/stock/market/members")
  assert.equal(observed.searchParams.get("industry"), "白酒")
  assert.equal(observed.searchParams.get("offset"), "50")
  assert.equal(observed.searchParams.get("limit"), "50")
  assert.equal(observed.searchParams.get("data_only"), "true")
  assert.equal(result.items[0].close, null)
})
test("market payloads reject invalid prices rather than rendering misleading values", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ ...metadata, total: 1, has_more: false, boards: [{
    industry: "白酒", count: 20, quoted_count: 3, comparable_count: 3, advancing: 2, declining: 1, flat: 0,
    change_pct: "10%", leader: null,
  }] }))
  await assert.rejects(api.getStockBoardsApi(), /行情响应格式/)
})
test("stock detail enforces a standard code before any network call", async () => {
  let requests = 0
  globalThis.fetch = async () => { requests += 1; return new Response("{}") }
  await assert.rejects(api.getStockDetailsApi("IF2606"), /六位/)
  assert.equal(requests, 0)
})

test("EOD provenance survives schema parsing while legacy qfq metadata stays optional", async () => {
  const provenance = { source: "teajoin daily 盘后快照", source_row_count: 5567, missing_codes: ["000001", "000002"], unknown_codes: ["999999"], data_version: "a".repeat(64), collected_at: "2026-10-18T04:00:00+00:00", reference_price: "vendor_ex_rights_pre_close", license_scope: "operator_configured_account_not_redistribution_certified" }
  globalThis.fetch = async () => Response.json({ ...metadata, ...provenance, boards: [], total: 0, has_more: false })
  const result = await api.getStockBoardsApi()
  for (const [key, value] of Object.entries(provenance)) assert.deepEqual(result[key], value)
  globalThis.fetch = async () => Response.json({ ...metadata, boards: [], total: 0, has_more: false })
  const legacy = await api.getStockBoardsApi()
  assert.equal(legacy.price_adjustment, "qfq")
  for (const key of ["source_row_count", "missing_codes", "unknown_codes", "data_version", "collected_at"]) assert.equal(legacy[key], undefined)
})

test("invalid EOD diagnostics fail explicitly instead of fabricating complete coverage", async () => {
  for (const invalid of [{ source_row_count: -1 }, { missing_codes: "000001" }, { unknown_codes: [42] }, { collected_at: "not-a-date" }]) {
    globalThis.fetch = async () => Response.json({ ...metadata, ...invalid, boards: [], total: 0, has_more: false })
    await assert.rejects(api.getStockBoardsApi(), /行情响应格式/)
  }
})

const detail = {
  instrument: { code: "000001", instrument_id: "000001.SZ", exchange: "SZSE", name: "证券", industry: "银行", list_date: "1991-04-03" },
  as_of: "2026-10-21", coverage: { first_date: "2026-10-15", last_date: "2026-10-16", daily_rows: 2 },
  valuation: { trade_date: "2026-10-16", pe_ttm: -2, pb: -1, total_mv: 1, circ_mv: 1, turnover_rate: 0 },
  financials: [{ report_date: "2026-06-30", publish_date: "2026-08-20", roe: -2, gross_margin: null, revenue_yoy: 0, netprofit_yoy: -100, eps: -1, bps: -1 }], limitations: [],
}

test("detail rejects the wrong stock and exchange identity rather than attaching its financials", async () => {
  for (const instrument of [{ ...detail.instrument, code: "000002", instrument_id: "000002.SZ" },
    { ...detail.instrument, instrument_id: "000001.SH" }, { ...detail.instrument, exchange: "SSE" },
    { ...detail.instrument, instrument_id: "000001.SZSE" }]) {
    globalThis.fetch = async () => Response.json({ ...detail, instrument })
    await assert.rejects(api.getStockDetailsApi("000001"), /证券|行情响应/)
  }
})

test("detail rejects impossible dates, future disclosures and contradictory coverage", async () => {
  for (const value of [
    { ...detail, as_of: "2026-02-30" },
    { ...detail, coverage: { ...detail.coverage, first_date: "2026-10-17" } },
    { ...detail, coverage: { ...detail.coverage, daily_rows: 0 } },
    { ...detail, valuation: { ...detail.valuation, trade_date: "2026-10-22" } },
    { ...detail, financials: [{ ...detail.financials[0], publish_date: "2026-10-22" }] },
    { ...detail, financials: [{ ...detail.financials[0], publish_date: "2026-05-30" }] },
    { ...detail, financials: [detail.financials[0], detail.financials[0]] },
  ]) {
    globalThis.fetch = async () => Response.json(value)
    await assert.rejects(api.getStockDetailsApi("000001"), /行情响应/)
  }
})

test("valid loss metrics zero turnover and absent fundamentals remain real values", async () => {
  globalThis.fetch = async () => Response.json(detail)
  assert.deepEqual(await api.getStockDetailsApi("000001"), detail)
  const empty = { ...detail, coverage: { first_date: null, last_date: null, daily_rows: 0 }, valuation: null, financials: [] }
  globalThis.fetch = async () => Response.json(empty)
  assert.deepEqual(await api.getStockDetailsApi("000001"), empty)
})

const validBoard = { industry: "银行", count: 3, quoted_count: 2, comparable_count: 2, advancing: 1, declining: 1, flat: 0, change_pct: 0, leader: { code: "000001", name: "证券", change_pct: 1 } }
test("board contradictions and invalid calendar dates never become market statistics", async () => {
  for (const value of [
    { ...metadata, as_of: "2026-02-30" },
    { ...metadata, previous_date: "2026-10-17" },
    { ...metadata, coverage: { instruments: 1, quoted: 2, comparable: 2 } },
    { ...metadata, boards: [{ ...validBoard, comparable_count: 3 }] },
    { ...metadata, boards: [{ ...validBoard, advancing: 0 }] },
    { ...metadata, boards: [{ ...validBoard, comparable_count: 0, advancing: 0, declining: 0 }] },
    { ...metadata, boards: [validBoard, validBoard] },
  ]) {
    globalThis.fetch = async () => Response.json({ boards: [validBoard], total: 3, has_more: false, ...value })
    await assert.rejects(api.getStockBoardsApi(), /行情响应/)
  }
})

test("member states cannot present mismatched identifiers negative volume or zero prices as valid quotes", async () => {
  const row = { code: "000001", instrument_id: "000001.SZ", exchange: "SZSE", name: "证券", industry: "银行", close: 10, change: 0, change_pct: 0, volume: 0, trade_date: "2026-10-16", data_available: true, data_status: "ready" }
  for (const items of [[{ ...row, instrument_id: "000002.SZ" }], [{ ...row, close: 0 }], [{ ...row, volume: -1 }],
    [{ ...row, data_available: false }], [{ ...row, trade_date: "2026-10-17" }], [row, row]]) {
    globalThis.fetch = async () => Response.json({ ...metadata, items, total: 2, has_more: false })
    await assert.rejects(api.getStockMembersApi(), /行情响应/)
  }
  globalThis.fetch = async () => Response.json({ ...metadata, items: [row], total: 1, has_more: false })
  assert.equal((await api.getStockMembersApi()).items[0].volume, 0)
})

test("visible industry counts cannot exceed the global security coverage", async () => {
  globalThis.fetch = async () => Response.json({ ...metadata, coverage: { instruments: 2, quoted: 2, comparable: 2 }, boards: [validBoard], total: 1, has_more: false })
  await assert.rejects(api.getStockBoardsApi(), /行情响应/)
})

test("invalid member offsets stop before network access", async () => {
  let calls = 0
  globalThis.fetch = async () => { calls++; return Response.json({}) }
  for (const offset of [-1, 0.5, Number.NaN, 10001]) await assert.rejects(api.getStockMembersApi({ offset }), /分页/)
  assert.equal(calls, 0)
})

test("normal members API enforces its requested fifty-row page independently of screen capacity", async () => {
  const items = Array.from({ length: 51 }, (_, index) => {
    const code = String(index + 1).padStart(6, "0")
    return { code, instrument_id: `${code}.SZ`, exchange: "SZSE", name: "证券", industry: "银行", close: 10, change: 0, change_pct: 0, volume: 0, trade_date: "2026-10-16", data_available: true, data_status: "ready" }
  })
  globalThis.fetch = async () => Response.json({ ...metadata, items, total: 51, has_more: false })
  await assert.rejects(api.getStockMembersApi(), /分页数量/)
  globalThis.fetch = async () => Response.json({ ...metadata, items: items.slice(0, 50), total: 51, has_more: true })
  assert.equal((await api.getStockMembersApi()).items.length, 50)
})
