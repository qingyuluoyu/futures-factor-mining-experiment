import assert from "node:assert/strict"
import test from "node:test"

const api = await import("./stock-custom-factor-api.ts")
const identifier = "b6372fe1-e9ee-4d12-b09c-929922f1d7e3"
const key = "f682700b-7415-47d6-84c0-305ec50490cd"
const view = { id: identifier, revision: 1, latest_revision: 1, name: "动量", expression: "close", expression_version: "a".repeat(64), formula_version: "stock-expression-v1", fields: ["close"], lookback: 0, direction: "high", description: "", disabled: false, created_at: "2026-10-23T02:00:00Z" }

async function withFetch(handler, callback) {
  const original = globalThis.fetch
  globalThis.fetch = handler
  try { await callback() } finally { globalThis.fetch = original }
}

test("custom factor client validates commands, uses scoped no-store auth and preserves provided idempotency keys", async () => {
  const calls = []
  await withFetch(async (url, options) => { calls.push({ url, options }); return Response.json(view) }, async () => {
    const body = { idempotency_key: key, name: "动量", expression: "close", direction: "high", description: "" }
    assert.equal((await api.saveCustomFactor(body, "token")).id, identifier)
    assert.equal(JSON.parse(calls[0].options.body).idempotency_key, key)
    assert.equal(calls[0].options.headers.Authorization, "Bearer token")
    assert.equal(calls[0].options.cache, "no-store")
    assert.equal(calls[0].options.method, "POST")
    assert.ok(calls[0].options.signal)
    await assert.rejects(api.saveCustomFactor({ ...body, direction: "bad" }, "token"))
    await assert.rejects(api.saveCustomFactor(body, ""), /登录/)
    assert.equal(calls.length, 1)
  })
})

test("read requests reject mismatched versions and impossible pagination rather than silently using latest", async () => {
  await withFetch(async () => Response.json(view), async () => {
    assert.equal((await api.getCustomFactorVersion(identifier, 1, "token")).revision, 1)
    await assert.rejects(api.getCustomFactorVersion(identifier, 2, "token"), /版本/)
  })
  await withFetch(async () => Response.json({ items: [view], total: 0, has_more: false }), async () => {
    await assert.rejects(api.listCustomFactors({ limit: 20, offset: 0 }, "token"), /分页/)
  })
})

test("structured failures surface a safe message and request id without automatic retry", async () => {
  let calls = 0
  await withFetch(async () => { calls++; return Response.json({ detail: { code: "factor_revision_conflict", message: "请刷新后编辑", request_id: "trace-id", retryable: false } }, { status: 409 }) }, async () => {
    await assert.rejects(api.disableCustomFactor(identifier, { idempotency_key: key, expected_revision: 1 }, "token"), (error) => error.status === 409 && error.requestId === "trace-id" && error.message.includes("请刷新"))
    assert.equal(calls, 1)
  })
})

test("response bounds and schema stop malformed or oversized payloads", async () => {
  await withFetch(async () => new Response("x".repeat(1100000)), async () => {
    await assert.rejects(api.getCustomFactorAvailability("token"), /大小/)
  })
  await withFetch(async () => Response.json({ ...view, lookback: 245 }), async () => {
    await assert.rejects(api.getCustomFactorVersion(identifier, 1, "token"))
  })
})

test("last representable stored revision remains readable although it cannot be edited again", async () => {
  await withFetch(async () => Response.json({ ...view, revision: 2147483647, latest_revision: 2147483647 }), async () => {
    assert.equal((await api.getCustomFactorVersion(identifier, 2147483647, "token")).revision, 2147483647)
  })
  await withFetch(async () => Response.json({ ...view, revision: 2147483647, latest_revision: 2147483647, disabled: true }), async () => {
    assert.equal((await api.disableCustomFactor(identifier, { idempotency_key: key, expected_revision: 2147483647 }, "token")).disabled, true)
  })
})
