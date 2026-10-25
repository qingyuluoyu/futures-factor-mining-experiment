import assert from "node:assert/strict"
import test from "node:test"

globalThis.localStorage = { getItem: () => "qa-test-token" }
globalThis.window = { location: { href: "" } }

const view = {
  id: "account-1", name: "回放", market_type: "stock", trading_mode: "virtual",
  method_version: "stock-replay-v1", price_adjustment: "none", currency: "CNY",
  initial_cash: "1000000.00", cash: "1000000.00", available_cash: "1000000.00",
  reserved_cash: "0.00", equity: "1000000.00", total_pnl: "0.00", fees_paid: "0.00", realized_pnl: "0.00",
  current_date: "2026-10-09", start_date: "2026-10-08", end_date: "2026-10-12", status: "active", revision: 0,
  positions: [], orders: [], instruments: [{ code: "000001", name: "平安银行", instrument_id: "000001.SZ" }],
  chart: { code: "000001", adjustment: "none", volume_unit: "share", bars: [
    { time: "2026-10-08", open: 10, high: 11, low: 9, close: 10.5, volume: 10000 },
  ] }, data_versions: ["snapshot-v1"],
}

test("replay API keeps all account reads and writes inside the stock replay namespace", async () => {
  const api = await import("./stock-replay-api.ts")
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    if (url.endsWith("/availability")) return Response.json({ enabled: true, datasets: [] })
    if (url.includes("?limit=")) return Response.json([])
    return Response.json(view)
  }
  await api.getStockReplayAvailability()
  await api.listStockReplayAccounts()
  await api.getStockReplayAccount("account/1", "000001")
  const create = { idempotency_key: crypto.randomUUID(), name: "回放", initial_cash: "1000000", start_date: "2026-10-08", dataset_ids: [crypto.randomUUID()] }
  await api.createStockReplayAccount(create)
  const command = { idempotency_key: crypto.randomUUID(), expected_revision: 0 }
  await api.submitStockReplayOrder("account-1", { ...command, code: "000001", direction: "buy", quantity: 100, order_type: "market", limit_price: null })
  await api.advanceStockReplayAccount("account-1", command)
  await api.cancelStockReplayOrder("account-1", "order/1", command)
  assert.deepEqual(calls.map(({ url }) => url), [
    "/api/stock/replay/availability", "/api/stock/replay/accounts?limit=20&offset=0",
    "/api/stock/replay/accounts/account%2F1?code=000001", "/api/stock/replay/accounts",
    "/api/stock/replay/accounts/account-1/orders", "/api/stock/replay/accounts/account-1/step",
    "/api/stock/replay/accounts/account-1/orders/order%2F1/cancel",
  ])
  assert.ok(calls.every(({ options }) => options.signal instanceof AbortSignal))
  assert.ok(calls.slice(3).every(({ options }) => options.method === "POST"))
  assert.deepEqual(JSON.parse(calls[5].options.body), command)
})

test("replay API refuses malformed money, future candles and wrong execution units", async () => {
  const { getStockReplayAccount } = await import("./stock-replay-api.ts")
  for (const invalid of [
    { ...view, equity: 1000000 },
    { ...view, cash: "NaN" },
    { ...view, chart: { ...view.chart, volume_unit: "lot" } },
    { ...view, chart: { ...view.chart, bars: [{ ...view.chart.bars[0], time: "2026-10-12" }] } },
    { ...view, chart: { ...view.chart, bars: [{ ...view.chart.bars[0], high: 8 }] } },
  ]) {
    globalThis.fetch = async () => Response.json(invalid)
    await assert.rejects(getStockReplayAccount("account-1"), /回放数据/)
  }
})

test("replay API surfaces disabled and failed availability without fallback or automatic retries", async () => {
  const { getStockReplayAvailability } = await import("./stock-replay-api.ts")
  globalThis.fetch = async () => Response.json({ enabled: false, datasets: [] })
  assert.equal((await getStockReplayAvailability()).enabled, false)
  let calls = 0
  globalThis.fetch = async () => {
    calls += 1
    return Response.json({ detail: "回放数据尚未迁移" }, { status: 503 })
  }
  await assert.rejects(getStockReplayAvailability(), (error) => error.status === 503 && /尚未迁移/.test(error.message))
  assert.equal(calls, 1)
})

test("replay view accepts all 100 pending orders plus 200 historical orders and refuses unbounded output", async () => {
  const { getStockReplayAccount } = await import("./stock-replay-api.ts")
  const orders = Array.from({ length: 300 }, (_, index) => ({
    id: `order-${index}`, code: "000001", direction: "buy", quantity: 100,
    order_type: "market", limit_price: null, status: index < 100 ? "pending" : "filled",
    reason: null, submitted_on: "2026-10-08", filled_on: index < 100 ? null : "2026-10-09",
    filled_price: index < 100 ? null : "10.00", fees: index < 100 ? "0.00" : "5.00",
    cash_delta: index < 100 ? null : "-1005.00", realized_pnl: "0.00",
  }))
  globalThis.fetch = async () => Response.json({ ...view, orders })
  const result = await getStockReplayAccount("account-1")
  assert.equal(result.orders.filter((order) => order.status === "pending").length, 100)
  assert.equal(result.orders.filter((order) => order.status === "filled").length, 200)
  globalThis.fetch = async () => Response.json({ ...view, orders: [...orders, { ...orders[0], id: "excess-order" }] })
  await assert.rejects(getStockReplayAccount("account-1"), /回放数据/)
})
