import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"

function load(reply) {
  const path = new URL("../app/api/ai/chat/[[...path]]/route.ts", import.meta.url)
  assert.equal(existsSync(path), true, "streaming chat route missing")
  const module = { exports: {} }
  const calls = []
  const output = ts.transpileModule(readFileSync(path, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  vm.runInNewContext(output, { module, exports: module.exports, process: { env: { BACKEND_URL: "http://127.0.0.1:8002" } }, Response, Headers, URL, AbortSignal, TextDecoder,
    fetch: async (...arguments_) => { calls.push(arguments_); return reply() },
  })
  return { route: module.exports, calls }
}

test("SSE body remains streamed and auth is forwarded once", async () => {
  const page = load(() => new Response('event: done\ndata: {}\n\n', { headers: { "Content-Type": "text/event-stream", "X-Request-ID": "fixture" } }))
  const response = await page.route.POST(new Request("http://local/api/ai/chat", { method: "POST", headers: { Authorization: "Bearer fixture" }, body: "{}" }), { params: Promise.resolve({}) })
  assert.equal(response.status, 200)
  assert.match(response.headers.get("Content-Type"), /text\/event-stream/)
  assert.equal(page.calls.length, 1)
  assert.equal(page.calls[0][1].headers.get("Authorization"), "Bearer fixture")
  assert.equal(await response.text(), 'event: done\ndata: {}\n\n')
})

test("arbitrary backend path is not proxied", async () => {
  const page = load(() => { throw new Error("must not call") })
  const response = await page.route.GET(new Request("http://local/api/ai/chat/secret"), { params: Promise.resolve({ path: ["secret"] }) })
  assert.equal(response.status, 404)
  assert.equal(page.calls.length, 0)
})

test("unknown POST transport outcome is not automatically retried", async () => {
  const page = load(() => { throw new Error("private transport detail") })
  const response = await page.route.POST(new Request("http://local/api/ai/chat", { method: "POST", body: "{}" }), { params: Promise.resolve({}) })
  assert.equal(response.status, 503)
  assert.equal(page.calls.length, 1)
  assert.equal((await response.text()).includes("private transport"), false)
})
