import assert from "node:assert/strict"
import test from "node:test"

test("stock market links round-trip industry and six-digit stock code without adding arbitrary query keys", async () => {
  const { stockMarketHref, parseStockIndustry } = await import("./stock-market-navigation.ts")
  assert.equal(stockMarketHref(), "/market")
  assert.equal(stockMarketHref("", "600001"), "/market?symbol=600001")
  for (const industry of ["银行", "软件 & 服务", "A/B+产业", "甲".repeat(32)]) {
    const href = stockMarketHref(industry, "000001")
    const url = new URL(href, "https://stock.invalid")
    assert.equal(url.pathname, "/market")
    assert.deepEqual([...url.searchParams.keys()], ["industry", "symbol"])
    assert.equal(url.searchParams.get("industry"), industry)
    assert.equal(parseStockIndustry(url.searchParams.get("industry")).value, industry)
  }
  for (const code of ["abc", "600001.SH", "000001&admin=true"]) assert.throws(() => stockMarketHref("银行", code))
})

test("industry URLs preserve the API 32-character bound and reject invalid scope instead of silently widening it", async () => {
  const { stockMarketHref, parseStockIndustry } = await import("./stock-market-navigation.ts")
  assert.equal(parseStockIndustry(null).value, "")
  assert.equal(parseStockIndustry("  银行  ").value, "银行")
  assert.equal(parseStockIndustry("甲".repeat(33)).value, undefined)
  assert.equal(typeof parseStockIndustry("甲".repeat(33)).error, "string")
  assert.equal(typeof parseStockIndustry("银\n行").error, "string")
  assert.throws(() => stockMarketHref("甲".repeat(33)))
})
