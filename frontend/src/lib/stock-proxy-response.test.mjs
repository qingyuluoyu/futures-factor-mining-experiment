import assert from "node:assert/strict"
import test from "node:test"

const { stockProxyResponse } = await import("./stock-proxy-response.ts")
test("stock proxy preserves research tracing and error contract but not upstream private headers", async () => {
  const upstream = new Response('{"detail":"missing price"}', { status: 409, headers: {
    "content-type": "application/json", "x-request-id": "qa-trace", "x-research-error": "missing_held_price",
    "x-method-version": "v2", "set-cookie": "private=secret", "cache-control": "public,max-age=3600",
  } })
  const response = stockProxyResponse(upstream, await upstream.text())
  assert.equal(response.status, 409)
  assert.equal(response.headers.get("x-request-id"), "qa-trace")
  assert.equal(response.headers.get("x-research-error"), "missing_held_price")
  assert.equal(response.headers.get("x-method-version"), "v2")
  assert.equal(response.headers.get("set-cookie"), null)
  assert.equal(response.headers.get("cache-control"), "no-store")
})

test("stock proxy preserves CSV BOM bytes and safe attachment security headers", async () => {
  const bytes = new TextEncoder().encode("\uFEFFsection,name\r\ntrades,中文\r\n")
  const attachment = 'attachment; filename="stock-research-11111111-1111-4111-8111-111111111111.csv"'
  const upstream = new Response(bytes, { headers: { "Content-Type": "text/csv; charset=utf-8", "Content-Disposition": attachment, "X-Content-Type-Options": "nosniff" } })
  const response = stockProxyResponse(upstream, await upstream.arrayBuffer())
  assert.deepEqual(new Uint8Array(await response.arrayBuffer()), bytes)
  assert.equal(response.headers.get("Content-Disposition"), attachment)
  assert.equal(response.headers.get("X-Content-Type-Options"), "nosniff")
})
