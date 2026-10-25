import assert from "node:assert/strict"
import test from "node:test"

const { validateStockQuoteBars } = await import("./stock-quote-data.ts")
test("detail and chart refuse inconsistent latest OHLC instead of displaying different quotes", () => {
  const valid = { time: "2026-10-15", open: 10, high: 11, low: 9, close: 11, volume: 100 }
  assert.throws(() => validateStockQuoteBars([valid, { ...valid, time: "2026-10-16", close: 999 }]), /日线数据异常/)
  assert.deepEqual(validateStockQuoteBars([valid]), [valid])
})
