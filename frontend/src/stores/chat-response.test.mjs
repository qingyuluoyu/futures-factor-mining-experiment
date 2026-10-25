import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import vm from "node:vm"
import ts from "typescript"

const requireModule = createRequire(import.meta.url)
function load(name, overrides = {}, extra = "") {
  const module = { exports: {} }
  const source = readFileSync(new URL(name, import.meta.url), "utf8") + extra
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const storage = new Map()
  const auth = { user: { id: "fixture", market_type: "futures" }, accessToken: `header.${Buffer.from(JSON.stringify({ sub: "fixture", trading_mode: "virtual" })).toString("base64url")}.signature`, sessionRevision: 1 }
  const dependencies = { "@/stores/auth": { useAuthStore: { getState: () => auth, subscribe: () => () => {} } }, "@/stores/ai-settings": { useAISettingsStore: { getState: () => ({ models: [], providers: [] }), subscribe: () => () => {} } }, "@/lib/ai-client-scope": loadScope(), ...overrides }
  const sandbox = { module, exports: module.exports, AbortController, AbortSignal, window: { innerWidth: 1440, innerHeight: 1000 }, localStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) }, require: (name) => name in dependencies ? dependencies[name] : requireModule(name) }
  vm.runInNewContext(output, sandbox)
  return { exports: module.exports, sandbox, storage }
}

function loadScope() {
  const module = { exports: {} }
  const source = readFileSync(new URL("../lib/ai-client-scope.ts", import.meta.url), "utf8")
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, { module, exports: module.exports, atob })
  return module.exports
}

test("chat window starts readable, supports wide mode and fits narrow viewports", () => {
  const loaded = load("./ai-float.ts")
  const store = loaded.exports.useAiFloatStore
  store.getState().hydrate()
  assert.equal(store.getState().width, 640)
  store.getState().toggleWide()
  assert.equal(store.getState().width, 1120)
  store.getState().toggleWide()
  assert.equal(store.getState().width, 640)
  loaded.sandbox.window.innerWidth = 320
  loaded.sandbox.window.innerHeight = 400
  store.getState().setSize(900, 900)
  store.getState().setWinOffset(999, 999)
  const state = store.getState()
  assert.ok(state.width <= 304 && state.height <= 336)
  assert.ok(state.winRight >= 0 && state.winRight + state.width <= 320)
  assert.ok(state.winBottom >= 0 && state.winBottom + state.height <= 400)
})

test("context always ends with the current prompt, not the previous successful turn", () => {
  const { exports } = load("./ai-chat.ts", { "@/lib/api": {}, "@/lib/ai-stream": {}, "@/stores/indicator": {} }, "\nexport { buildContext }\n")
  const messages = [{ role: "user", content: "旧问题" }, { role: "assistant", content: "旧回答", isStreaming: false }, { role: "user", content: "新的问题" }, { role: "assistant", content: "", isStreaming: true }]
  assert.equal(exports.buildContext(messages).at(-1)?.content, "新的问题")
  assert.equal(exports.buildContext([{ role: "user", content: "单独问题" }, { role: "assistant", content: "", isStreaming: true }]).at(-1)?.content, "单独问题")
})

test("request creation failure is visible and clears busy state", async () => {
  const { exports } = load("./ai-chat.ts", { "@/lib/api": { createConversation: async () => { throw new Error("private upstream detail") } }, "@/lib/ai-stream": {}, "@/stores/indicator": {} })
  const store = exports.useAIChatStore
  store.setState({ selectedModel: { model_id: "fixture" } })
  await store.getState().sendMessage("问题")
  assert.equal(store.getState().isStreaming, false)
  assert.match(store.getState().messages.at(-1)?.content ?? "", /无法创建对话/)
  assert.doesNotMatch(store.getState().messages.at(-1)?.content ?? "", /private upstream/)
})

test("partial answer retains a visible failure and is excluded from future context", async () => {
  const { exports } = load("./ai-chat.ts", { "@/lib/api": {}, "@/lib/ai-stream": { streamChat: async (_model, _messages, callbacks) => { callbacks.onMessage("未完成回答"); callbacks.onError("TPM/RPM限流"); callbacks.onDone() } }, "@/stores/indicator": { useIndicatorStore: { getState: () => ({ config: {} }) } } }, "\nexport { buildContext }\n")
  const store = exports.useAIChatStore
  store.setState({ selectedModel: { model_id: "fixture" }, conversationId: "fixture", loadConversations: async () => {} })
  await store.getState().sendMessage("问题")
  const answer = store.getState().messages.at(-1)
  assert.equal(answer.content, "未完成回答")
  assert.equal(answer.error, "TPM/RPM限流")
  assert.equal(exports.buildContext(store.getState().messages).length, 1)
})

test("streamed errors terminate once even with a second upstream error and done", async () => {
  const loaded = load("../lib/ai-stream.ts")
  let errors = 0, done = 0
  Object.assign(loaded.sandbox, { process: { env: {} }, TextDecoder, fetch: async () => new Response('event: error\r\ndata: {"message":"限流"}\r\n\r\nevent: error\r\ndata: {"message":"重复"}\r\n\r\nevent: done\r\ndata: {}\r\n\r\n') })
  await loaded.exports.streamChat("fixture", [], { onError: () => errors++, onDone: () => done++ })
  assert.equal(errors, 1)
  assert.equal(done, 1)
})

test("SSE uses captured credentials while the legacy signature remains compatible", async () => {
  const loaded = load("../lib/ai-stream.ts")
  const tokens = []
  loaded.storage.set("access_token", "replacement-synthetic-token")
  Object.assign(loaded.sandbox, { process: { env: {} }, TextDecoder, fetch: async (_url, request) => { tokens.push(request.headers.Authorization); return new Response('event: done\r\ndata: {}\r\n\r\n') } })
  await loaded.exports.streamChat("fixture", [], { onDone() {} }, { accessToken: "captured-synthetic-token", conversationId: "original" })
  await loaded.exports.streamChat("fixture", [], { onDone() {} }, "legacy")
  assert.deepEqual(tokens, ["Bearer captured-synthetic-token", "Bearer replacement-synthetic-token"])
})

test("owned-row stream probes capability with captured credentials and never posts to an incompatible server", async () => {
  const loaded = load("../lib/ai-stream.ts")
  const requests = [], errors = []
  let compatible = false
  Object.assign(loaded.sandbox, { process: { env: {} }, TextDecoder, AbortSignal, fetch: async (url, request) => {
    requests.push({ url, request })
    return url.endsWith("capabilities") ? new Response(JSON.stringify({ model_routing: compatible ? "owned-model-row-v1" : "old" })) : new Response('event: done\r\ndata: {}\r\n\r\n')
  } })
  const callbacks = { onError: (error) => errors.push(error), onDone() {} }
  await loaded.exports.streamChat("fixture", [], callbacks, { accessToken: "captured", modelRecordId: "selected-row" })
  assert.equal(requests.length, 1)
  assert.equal(errors.length, 1)
  compatible = true
  await loaded.exports.streamChat("fixture", [], callbacks, { accessToken: "captured", modelRecordId: "selected-row" })
  assert.equal(requests.length, 3)
  assert.equal(requests[2].request.headers.Authorization, "Bearer captured")
  assert.equal(JSON.parse(requests[2].request.body).model_record_id, "selected-row")
})

test("broken capability responses stay non-billable and never expose response fragments", async () => {
  for (const failure of ["invalid-json", "network", "404"]) {
    const loaded = load("../lib/ai-stream.ts")
    const errors = []
    let requests = 0
    Object.assign(loaded.sandbox, { process: { env: {} }, AbortSignal, fetch: async () => {
      requests++
      if (failure === "network") throw new Error("private-network-detail")
      return new Response("private-response-fragment", { status: failure === "404" ? 404 : 200 })
    } })
    await loaded.exports.streamChat("fixture", [], { onError: (message) => errors.push(message), onDone() {} }, { modelRecordId: "selected-row" })
    assert.equal(requests, 1)
    assert.equal(errors.length, 1)
    assert.match(errors[0], /未调用模型/)
    assert.doesNotMatch(errors[0], /private-/)
  }
})

test("saved user width is retained instead of forcibly reset", () => {
  const loaded = load("./ai-float.ts")
  loaded.storage.set("ai:float-window", JSON.stringify({ width: 520, height: 620, winRight: 16, winBottom: 16 }))
  loaded.exports.useAiFloatStore.getState().hydrate()
  assert.equal(loaded.exports.useAiFloatStore.getState().width, 520)
})

test("message bubbles wrap long tokens and display partial-output failures separately", () => {
  const { exports } = load("../components/ai/message-renderer.tsx", { "@/stores/ai-chat": { useAIChatStore: (selector) => selector({ sendMessage() {} }) }, "@/stores/display": {}, "./stream-status": {}, "./subagent-list": {}, "lightweight-charts": {} })
  const message = { role: "assistant", content: "尚未完成的分析", error: "供应商TPM/RPM限流", toolCalls: [], toolResults: [], confirmations: [], subAgents: [], isStreaming: false }
  const tree = exports.MessageBubble({ message })
  const nodes = (item) => Array.isArray(item) ? item.flatMap(nodes) : item && typeof item === "object" ? [item, ...nodes(item.props?.children)] : []
  const alert = nodes(tree).find((node) => node.props?.role === "alert")
  assert.equal(alert.props.children, "供应商TPM/RPM限流")
  assert.match(tree.props.className, /min-w-0/)
  assert.match(alert.props.className, /overflow-wrap:anywhere/)
})
