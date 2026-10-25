import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"

async function execute(body, options = {}, status = 200, exposeTrace = true) {
  const module = { exports: {} }
  const calls = []
  const output = ts.transpileModule(readFileSync(new URL("./ai-stream.ts", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  vm.runInNewContext(output, { module, exports: module.exports, process: { env: {} }, AbortSignal, TextDecoder, setTimeout, clearTimeout,
    fetch: async () => new Response(body, { status, headers: { "content-type": "text/event-stream", ...(exposeTrace ? { "X-Request-ID": "fixture-request" } : {}) } }),
  })
  const callbacks = Object.fromEntries(["onMessage", "onThinking", "onToolCall", "onToolResult", "onConfirmationRequest", "onReactRound", "onSubagentPlan", "onSubagentUpdate", "onError", "onDone"].map((name) => [name, (...args) => calls.push({ name, args })]))
  await module.exports.streamChat("fixture", [{ role: "user", content: "fixture" }], callbacks, { accessToken: "fixture", ...options })
  return calls
}

test("structured upstream failure preserves cooldown and trace", async () => {
  const calls = await execute('event: error\ndata: {"message":"limited","error_code":"provider_rate_limited","retry_after_seconds":17,"upstream_status":429}\n\n')
  const failure = calls.find((call) => call.name === "onError")
  assert.equal(failure.args[1]?.error_code, "provider_rate_limited")
  assert.equal(failure.args[1]?.retry_after_seconds, 17)
  assert.equal(failure.args[1]?.request_id, "fixture-request")
})

test("known client request id survives a cross-origin hidden trace header", async () => {
  const requestId = "11faa4f4-706c-4867-9ede-c56a30642fc5"
  const calls = await execute('event: error\ndata: {"message":"limited","error_code":"provider_rate_limited"}\n\n', { requestId }, 200, false)
  assert.equal(calls.find((call) => call.name === "onError").args[1].request_id, requestId)
})

test("empty EOF is incomplete, not success", async () => {
  const calls = await execute("")
  assert.equal(calls.filter((call) => call.name === "onError").length, 1)
  assert.equal(calls.find((call) => call.name === "onError").args[1]?.error_code, "stream_incomplete")
})

test("partial answer with no terminal event remains explicitly incomplete", async () => {
  const calls = await execute('event: message\ndata: {"content":"部分回答"}\n\n')
  assert.equal(calls[0].args[0], "部分回答")
  assert.equal(calls.filter((call) => call.name === "onError").length, 1)
})

test("done terminates once and comments do not become messages", async () => {
  const calls = await execute(': heartbeat\n\nevent: message\ndata: {"content":"完成"}\n\nevent: done\ndata: {}\n\nevent: done\ndata: {}\n\n')
  assert.equal(calls.filter((call) => call.name === "onError").length, 0)
  assert.equal(calls.filter((call) => call.name === "onDone").length, 1)
})

test("malformed terminal data cannot pretend success", async () => {
  const calls = await execute('event: done\ndata: {broken}\n\n')
  assert.equal(calls.filter((call) => call.name === "onError").length, 1)
})

test("HTTP structured error does not expose arbitrary upstream details", async () => {
  const calls = await execute(JSON.stringify({ detail: "already submitted", error_code: "chat_request_exists", retry_after_seconds: 5, secret: "not-returned" }), {}, 409)
  assert.equal(calls[0].args[0], "already submitted")
  assert.equal(calls[0].args[1]?.error_code, "chat_request_exists")
  assert.equal(JSON.stringify(calls).includes("not-returned"), false)
})
