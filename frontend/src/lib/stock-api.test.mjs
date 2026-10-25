import assert from "node:assert/strict"
import test from "node:test"

globalThis.localStorage = { getItem: () => "qa-test-token" }
globalThis.window = { location: { href: "" } }
const { getStockUniverseApi, getStockKlineApi } = await import("./stock-api.ts")

test("stock universe loads bounded pages rather than silently losing later symbols", async () => {
  const requests = []
  globalThis.fetch = async (input) => {
    const url = new URL(String(input), "http://localhost")
    requests.push(url)
    const page = url.searchParams.get("offset") === "0"
      ? Array.from({ length: 500 }, (_, index) => ({ code: String(index).padStart(6, "0"), name: "QA" }))
      : [{ code: "600519", name: "贵州茅台" }]
    return new Response(JSON.stringify(page))
  }
  const result = await getStockUniverseApi()
  assert.equal(result.length, 501)
  assert.equal(result.at(-1).code, "600519")
  assert.deepEqual(requests.map((url) => url.searchParams.get("offset")), ["0", "500"])
  assert.ok(requests.every((url) => url.searchParams.get("limit") === "500"))
})

test("stock universe refuses silent truncation when the explicit loading ceiling is exceeded", async () => {
  let calls = 0
  globalThis.fetch = async () => {
    calls += 1
    return new Response(JSON.stringify(Array.from({ length: 500 }, () => ({ code: "600519", name: "QA" }))))
  }
  await assert.rejects(getStockUniverseApi(), /10000/)
  assert.equal(calls, 20)
})

test("stock kline retains explicit adjustment source and lot units without converting values", async () => {
  const response = { code: "600001", name: "证券", industry: "银行", bars: [{ time: "2026-10-16", open: 10, high: 11, low: 9, close: 11, volume: 100 }], has_more: false, price_adjustment: "none", source: "teajoin_eod", volume_unit: "lot_100_shares" }
  const controller = new AbortController()
  let options, path
  globalThis.fetch = async (input, init) => { path = String(input); options = init; return new Response(JSON.stringify(response)) }
  assert.deepEqual(await getStockKlineApi("600001", 1500, controller.signal), response)
  assert.match(path, /code=600001&limit=1500/)
  controller.abort()
  assert.equal(options.signal.aborted, true)
})
