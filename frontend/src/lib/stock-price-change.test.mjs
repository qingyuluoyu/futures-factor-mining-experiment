import assert from "node:assert/strict"
import test from "node:test"

test("raw daily change uses vendor ex-rights reference even for a single bar or a gap", async () => {
  const { stockPriceChange } = await import("./stock-price-change.ts")
  const bar = { close: 11, pre_close: 10 }
  assert.deepEqual(stockPriceChange(bar, { close: 100 }, "vendor_ex_rights_pre_close"), { reference: 10, change: 1, changePct: 10 })
  assert.deepEqual(stockPriceChange(bar, undefined, "vendor_ex_rights_pre_close"), { reference: 10, change: 1, changePct: 10 })
})

test("missing invalid or unavailable raw reference never falls back to adjacent raw close", async () => {
  const { stockPriceChange } = await import("./stock-price-change.ts")
  for (const pre_close of [undefined, null, 0, -1, Infinity, NaN, "10"])
    assert.equal(stockPriceChange({ close: 11, pre_close }, { close: 10 }, "vendor_ex_rights_pre_close"), null)
  assert.equal(stockPriceChange({ close: 11, pre_close: 10 }, { close: 10 }, "unavailable"), null)
  assert.equal(stockPriceChange({ close: Infinity, pre_close: 10 }, undefined, "vendor_ex_rights_pre_close"), null)
})

test("adjusted price change uses only adjacent loaded adjusted bar and rejects overflow", async () => {
  const { stockPriceChange } = await import("./stock-price-change.ts")
  assert.deepEqual(stockPriceChange({ close: 11, pre_close: 2 }, { close: 10 }, "adjacent_loaded_adjusted_close"), { reference: 10, change: 1, changePct: 10 })
  assert.equal(stockPriceChange({ close: 11 }, undefined, "adjacent_loaded_adjusted_close"), null)
  assert.equal(stockPriceChange({ close: 1e308 }, { close: 1e-308 }, "adjacent_loaded_adjusted_close"), null)
})

test("fractional lot and tiny nonzero volume display never becomes zero", async () => {
  const { formatStockVolume } = await import("./stock-price-change.ts")
  assert.equal(formatStockVolume(0.01), "0.01")
  assert.equal(formatStockVolume(123.45), "123.45")
  assert.equal(formatStockVolume(0), "0")
  assert.equal(formatStockVolume(1e-9), "<0.000001")
  assert.equal(formatStockVolume(10000), "1.00万")
  assert.equal(formatStockVolume(1e8), "1.00亿")
  assert.equal(formatStockVolume(NaN), "—")
})
