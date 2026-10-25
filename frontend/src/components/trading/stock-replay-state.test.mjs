import assert from "node:assert/strict"
import test from "node:test"

test("remembered replay account is isolated by user and login mode", async () => {
  const { replayAccountStorageKey } = await import("./stock-replay-state.ts")
  assert.notEqual(replayAccountStorageKey("user-a", "live"), replayAccountStorageKey("user-b", "live"))
  assert.notEqual(replayAccountStorageKey("user-a", "live"), replayAccountStorageKey("user-a", "virtual"))
  assert.equal(replayAccountStorageKey("user-a", "virtual"), "stock-replay-account:user-a:virtual")
})

test("failed mutations retain their idempotency key and original expected revision for explicit retry", async () => {
  const { prepareReplayMutation } = await import("./stock-replay-state.ts")
  let created = 0
  const uuid = () => `uuid-${++created}`
  const first = prepareReplayMutation(null, "step:account-1", { expected_revision: 7 }, uuid)
  const retry = prepareReplayMutation(first, "step:account-1", { expected_revision: 7 }, uuid)
  assert.strictEqual(retry, first)
  assert.deepEqual(retry.body, { expected_revision: 7, idempotency_key: "uuid-1" })
  assert.equal(created, 1)
  assert.notEqual(prepareReplayMutation(first, "step:account-2", { expected_revision: 7 }, uuid).body.idempotency_key, first.body.idempotency_key)
})

test("create validation respects snapshot overlap, cash limits and 20 dataset maximum", async () => {
  const { replayCreateError } = await import("./stock-replay-state.ts")
  const datasets = [{ id: "first", start_date: "2026-10-08", end_date: "2026-10-12" }, { id: "second", start_date: "2026-10-09", end_date: "2026-10-16" }]
  const input = { name: "独立回放", initial_cash: "1000000.00", start_date: "2026-10-09", dataset_ids: ["first", "second"] }
  assert.equal(replayCreateError(input, datasets), null)
  assert.match(replayCreateError({ ...input, initial_cash: "99.99" }, datasets), /资金/)
  assert.match(replayCreateError({ ...input, initial_cash: "1000000000.01" }, datasets), /资金/)
  assert.match(replayCreateError({ ...input, initial_cash: "1e6" }, datasets), /资金/)
  assert.match(replayCreateError({ ...input, start_date: "2026-10-08" }, datasets), /日期/)
  assert.match(replayCreateError({ ...input, dataset_ids: [] }, datasets), /快照/)
  assert.match(replayCreateError({ ...input, dataset_ids: Array(21).fill("first") }, datasets), /20/)
  assert.match(replayCreateError(input, datasets.map((item) => ({ ...item, code: "000001" }))), /同一股票/)
})

test("orders validate shares, available holdings and limit price without futures rules", async () => {
  const { replayOrderError } = await import("./stock-replay-state.ts")
  const order = { code: "000001", direction: "buy", quantity: 100, order_type: "market", limit_price: null }
  assert.equal(replayOrderError(order, [{ code: "000001" }], []), null)
  assert.match(replayOrderError({ ...order, quantity: 99 }, [{ code: "000001" }], []), /100/)
  assert.match(replayOrderError({ ...order, quantity: 1000100 }, [{ code: "000001" }], []), /1,000,000/)
  assert.match(replayOrderError({ ...order, direction: "sell" }, [{ code: "000001" }], []), /可卖/)
  assert.match(replayOrderError({ ...order, order_type: "limit", limit_price: "0" }, [{ code: "000001" }], []), /限价/)
  assert.match(replayOrderError({ ...order, order_type: "limit", limit_price: "10.123" }, [{ code: "000001" }], []), /两位/)
  assert.match(replayOrderError({ ...order, code: "600001" }, [{ code: "000001" }], []), /快照/)
  assert.equal(replayOrderError({ ...order, direction: "sell" }, [{ code: "000001" }], [{ code: "000001", available_quantity: 100 }]), null)
})

test("order reasons explain conservative execution restrictions and preserve unknown source reasons", async () => {
  const { replayOrderReason } = await import("./stock-replay-state.ts")
  assert.match(replayOrderReason("limit_up"), /涨停/)
  assert.match(replayOrderReason("liquidity_limit"), /成交量/)
  assert.match(replayOrderReason("t_plus_one_or_insufficient_position"), /T\+1/)
  assert.equal(replayOrderReason("future-rule-code"), "future-rule-code")
  assert.equal(replayOrderReason(null), null)
})
