import assert from "node:assert/strict"
import test from "node:test"

const bar = { time: "2026-10-16", open: 10, high: 12, low: 9, close: 11, volume: 0.01, pre_close: 5 }
const response = { code: "600001", name: "证券", industry: "银行", bars: [bar], has_more: false,
  price_adjustment: "none", source: "teajoin_eod", volume_unit: "lot_100_shares", change_basis: "vendor_ex_rights_pre_close", reference_price: "vendor_ex_rights_pre_close" }

test("stock kline contract validates explicit raw reference and preserves fractional lots", async () => {
  const { parseStockKlineResponse } = await import("./stock-kline-contract.ts")
  assert.deepEqual(parseStockKlineResponse(response, "600001"), response)
})

test("legacy adjusted responses and missing raw references remain explicit not silently inferred", async () => {
  const { parseStockKlineResponse } = await import("./stock-kline-contract.ts")
  const { pre_close, ...adjusted } = bar
  const legacy = parseStockKlineResponse({ code: "600001", name: "证券", industry: "银行", bars: [adjusted], has_more: false }, "600001")
  assert.equal(legacy.change_basis, "adjacent_loaded_adjusted_close")
  const raw = parseStockKlineResponse({ ...response, bars: [adjusted], reference_price: undefined, change_basis: undefined }, "600001")
  assert.equal(raw.bars[0].pre_close, undefined)
  assert.equal(raw.change_basis, "vendor_ex_rights_pre_close")
})

test("stock kline rejects code drift unit or adjustment mixing malformed references and duplicates", async () => {
  const { parseStockKlineResponse } = await import("./stock-kline-contract.ts")
  for (const change of [{ code: "600002" }, { volume_unit: "share" }, { volume_unit: undefined }, { source: "local_import" },
    { price_adjustment: "qfq" }, { change_basis: "adjacent_loaded_adjusted_close" }, { reference_price: "adjacent" },
    { bars: [bar, bar] }, { bars: [{ ...bar, time: "2026-02-30" }] }, { bars: [{ ...bar, pre_close: 0 }] },
    { bars: [{ ...bar, pre_close: "5" }] }, { bars: [{ ...bar, high: 8 }] }, { bars: [{ ...bar, volume: NaN }] }])
    assert.throws(() => parseStockKlineResponse({ ...response, ...change }, "600001"), /日线响应/)
})

test("validated kline wrapper rejects bad input before fetching and binds cancel to old transport", async () => {
  globalThis.localStorage = { getItem: () => "synthetic-test-token" }
  globalThis.window = { location: { href: "" } }
  const { getValidatedStockKlineApi } = await import("./stock-kline-contract.ts")
  let calls = 0, signal
  globalThis.fetch = async (_, options) => { calls++; signal = options.signal; return new Response(JSON.stringify(response)) }
  await assert.rejects(getValidatedStockKlineApi("rb2701"), /股票代码/)
  await assert.rejects(getValidatedStockKlineApi("600001", 3001), /数量/)
  assert.equal(calls, 0)
  const controller = new AbortController()
  assert.deepEqual(await getValidatedStockKlineApi("600001", 250, controller.signal), response)
  controller.abort()
  assert.equal(signal.aborted, true)
  await assert.rejects(getValidatedStockKlineApi("600001", 250, controller.signal))
  assert.equal(calls, 1)
})

const coverage = { scope: "selected_source_local_store", available_bars: 5,
  first_date: "2026-10-12", last_date: "2026-10-16", requested_limit: 1, effective_limit: 1,
  window_status: "window_limited", history_completeness: "unverified" }

test("stock kline preserves optional selected local source coverage without certifying full history", async () => {
  const { parseStockKlineResponse } = await import("./stock-kline-contract.ts")
  assert.deepEqual(parseStockKlineResponse({ ...response, has_more: true, coverage }, "600001").coverage, coverage)
  const exhausted = { ...coverage, available_bars: 1, first_date: bar.time, requested_limit: 250, effective_limit: 250, window_status: "local_history_exhausted" }
  assert.deepEqual(parseStockKlineResponse({ ...response, coverage: exhausted }, "600001").coverage, exhausted)
  const empty = { ...exhausted, available_bars: 0, first_date: null, last_date: null, window_status: "no_local_history" }
  assert.deepEqual(parseStockKlineResponse({ ...response, bars: [], coverage: empty }, "600001").coverage, empty)
})

test("stock kline rejects contradictory coverage count range limits or completeness claims", async () => {
  const { parseStockKlineResponse } = await import("./stock-kline-contract.ts")
  for (const change of [{ available_bars: 0 }, { available_bars: 1.5 }, { first_date: null }, { last_date: "2026-10-15" },
    { first_date: "2026-10-17" }, { history_completeness: "complete" }, { scope: "vendor_full_history" },
    { effective_limit: 0 }, { effective_limit: 2 }, { window_status: "local_history_exhausted" }])
    assert.throws(() => parseStockKlineResponse({ ...response, has_more: true, coverage: { ...coverage, ...change } }, "600001"), /日线响应/)
  assert.throws(() => parseStockKlineResponse({ ...response, coverage }, "600001"), /日线响应/)
})

const sourceCatalog = { catalog_version: "stock-kline-sources-v1", code: "600001", default_mode: "legacy_auto", sources: [
  { id: "history_qfq", source: "local_import", price_adjustment: "qfq", available: true, reason: "available", available_bars: 100, first_date: "2026-01-01", last_date: "2026-10-15", window_cap: 3000, history_completeness: "unverified" },
  { id: "eod_raw", source: "teajoin_eod", price_adjustment: "none", available: true, reason: "available", available_bars: 5, first_date: "2026-10-12", last_date: "2026-10-16", window_cap: 250, history_completeness: "unverified" },
] }
test("source catalog validates fixed source semantics and chooses newest explicit source", async () => {
  const { parseStockKlineSources, latestStockKlineSource } = await import("./stock-kline-contract.ts")
  assert.deepEqual(parseStockKlineSources(sourceCatalog, "600001"), sourceCatalog)
  assert.equal(latestStockKlineSource(sourceCatalog), "eod_raw")
  const oldRaw = { ...sourceCatalog, sources: [sourceCatalog.sources[0], { ...sourceCatalog.sources[1], available: false, reason: "eod_disabled", available_bars: null, first_date: null, last_date: null }] }
  assert.equal(latestStockKlineSource(parseStockKlineSources(oldRaw, "600001")), "history_qfq")
  for (const change of [{ code: "600002" }, { sources: [sourceCatalog.sources[0], sourceCatalog.sources[0]] },
    { sources: [{ ...sourceCatalog.sources[0], source: "teajoin_eod" }, sourceCatalog.sources[1]] },
    { sources: [{ ...sourceCatalog.sources[0], available_bars: 0 }, sourceCatalog.sources[1]] }])
    assert.throws(() => parseStockKlineSources({ ...sourceCatalog, ...change }, "600001"), /来源目录/)
})
test("explicit kline transport binds source and refuses ignored selection without silently falling back", async () => {
  const { getValidatedStockKlineApi, getStockKlineSources } = await import("./stock-kline-contract.ts")
  let url
  globalThis.fetch = async (input) => { url = String(input); return new Response(JSON.stringify({ ...response, selected_source: "eod_raw" })) }
  const explicit = await getValidatedStockKlineApi("600001", 250, undefined, "eod_raw")
  assert.match(url, /source=eod_raw/)
  assert.equal(explicit.selected_source, "eod_raw")
  globalThis.fetch = async () => new Response(JSON.stringify(response))
  await assert.rejects(getValidatedStockKlineApi("600001", 250, undefined, "eod_raw"), /所选来源/)
  globalThis.fetch = async () => new Response(JSON.stringify(sourceCatalog))
  assert.deepEqual(await getStockKlineSources("600001"), sourceCatalog)
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 })
  await assert.rejects(getStockKlineSources("600001"), (error) => error.status === 404)
})
