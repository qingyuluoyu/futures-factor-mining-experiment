import assert from "node:assert/strict"
import test from "node:test"

let token = "paper-session-a"
globalThis.window = { location: { href: "" } }
globalThis.localStorage = { getItem: () => token }

test("custom funding accepts precise money and reuses the original pending command", async () => {
  const api = await import("./paper-api.ts")
  assert.equal(api.normalizePaperDepositAmount("1234567890123456.78"), "1234567890123456.78")
  assert.equal(api.normalizePaperDepositAmount("10"), "10.00")
  for (const amount of ["0", "-1", "1e6", "NaN", "1.001", "10000000000000000"])
    assert.throws(() => api.normalizePaperDepositAmount(amount), /金额/)
  const first = api.preparePaperDeposit(null, "100.00", token)
  assert.equal(first.body.amount, "100.00")
  assert.match(first.body.idempotency_key, /^[0-9a-f-]{36}$/)
  assert.strictEqual(api.preparePaperDeposit(first, "100", token), first)
  assert.throws(() => api.preparePaperDeposit(first, "101", token), /待确认/)
  assert.notEqual(api.preparePaperDeposit(first, "101", "paper-session-b").body.idempotency_key, first.body.idempotency_key)
})

test("deposit sends decimal strings with bounded HTTP and retries the caller's same key", async () => {
  const api = await import("./paper-api.ts")
  const calls = []
  const command = api.preparePaperDeposit(null, "2000000.25", token)
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    if (calls.length === 1) throw new TypeError("connection lost after commit")
    return Response.json({ balance: 2000000.25 })
  }
  await assert.rejects(api.depositPaperFunds(command.body, token), /connection lost/)
  await api.depositPaperFunds(command.body, token)
  assert.equal(calls.length, 2)
  assert.equal(calls[0].url, "/api/paper/account/deposit")
  assert.equal(calls[0].options.headers.Authorization, "Bearer paper-session-a")
  assert.ok(calls.every(({ options }) => options.signal instanceof AbortSignal))
  assert.equal(calls[0].options.body, calls[1].options.body)
  assert.deepEqual(JSON.parse(calls[0].options.body), command.body)
})

test("old sessions cannot send funding or apply late responses to a replacement login", async () => {
  const api = await import("./paper-api.ts")
  const command = api.preparePaperDeposit(null, "1.00", token)
  const originalToken = token
  let finish
  globalThis.fetch = () => new Promise((resolve) => { finish = resolve })
  const pending = api.depositPaperFunds(command.body, originalToken)
  token = "paper-session-b"
  finish(Response.json({ balance: 1 }))
  await assert.rejects(pending, /会话/)
  await assert.rejects(api.depositPaperFunds(command.body, originalToken), /会话/)
  token = originalToken
})

test("all futures account requests set a 20 second timeout and stop on abort", async () => {
  const api = await import("./paper-api.ts")
  const timeout = AbortSignal.timeout
  const requested = []
  AbortSignal.timeout = (duration) => {
    requested.push(duration)
    return AbortSignal.abort(new DOMException("request timeout", "TimeoutError"))
  }
  globalThis.fetch = async (_, options) => options.signal.throwIfAborted()
  try {
    await assert.rejects(api.getPaperAccount(token), /timeout/)
    assert.deepEqual(requested, [20000])
  } finally {
    AbortSignal.timeout = timeout
  }
})

test("a late unauthorized response from another session does not redirect the new user", async () => {
  const api = await import("./paper-api.ts")
  const originalToken = token
  let finish
  globalThis.fetch = () => new Promise((resolve) => { finish = resolve })
  const pending = api.getPaperAccount(originalToken)
  token = "replacement-session"
  finish(Response.json({ detail: "expired" }, { status: 401 }))
  await assert.rejects(pending, /会话/)
  assert.equal(window.location.href, "")
  token = originalToken
})

test("ambiguous timeout and gateway errors preserve pending deposit keys", async () => {
  const api = await import("./paper-api.ts")
  for (const status of [408, 429, 500, 502, 503, 504]) {
    assert.equal(api.isDefinitivePaperDepositError(new api.PaperRequestError("unknown outcome", status)), false)
  }
  for (const status of [400, 403, 409, 422]) {
    assert.equal(api.isDefinitivePaperDepositError(new api.PaperRequestError("rejected", status)), true)
  }
  assert.equal(api.isDefinitivePaperDepositError(new TypeError("network lost")), false)
})
