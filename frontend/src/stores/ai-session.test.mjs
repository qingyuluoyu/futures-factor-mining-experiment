import assert from "node:assert/strict"
import { readFileSync, existsSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"

const nativeRequire = createRequire(import.meta.url)
const model = (id = "row-a") => ({ id, model_id: "same-model-name", provider_id: "provider", display_name: id, capabilities: [] })
const token = (user, mode = "virtual", nonce = "") => `header.${Buffer.from(JSON.stringify({ sub: user, trading_mode: mode, nonce })).toString("base64url")}.signature`
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done }); return { promise, resolve } }

function setup(overrides = {}) {
  let auth = { user: { id: "user-a", market_type: "futures" }, accessToken: token("user-a"), sessionRevision: 1 }
  const subscriptions = []
  const storage = new Map()
  const calls = { streams: [], providers: 0, indicators: 0, conversations: 0 }
  const api = {
    getAIProviders: async () => { calls.providers++; return [] }, getAIModels: async () => [model()], getPresetProviders: async () => [],
    createAIProvider: async () => ({}), updateAIProvider: async () => ({}), deleteAIProvider: async () => ({}), testAIProvider: async () => ({ status: "success", models: [] }),
    batchCreateAIModels: async () => [], updateAIModel: async () => ({}), deleteAIModel: async () => ({}),
    createConversation: async () => ({ id: "conversation-a" }), getConversations: async () => { calls.conversations++; return [] },
    deleteConversation: async () => ({}), getConversationMessages: async () => ({ items: [], total: 0 }), ...overrides,
  }
  const loaded = {}
  const load = (file, name) => {
    const source = readFileSync(new URL(file, import.meta.url), "utf8")
    const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
    const module = { exports: {} }
    vm.runInNewContext(output, { module, exports: module.exports, AbortController, atob, console,
      localStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) },
      require: (specifier) => {
        if (specifier === "@/stores/auth") return { useAuthStore: { getState: () => auth, subscribe: (callback) => { subscriptions.push(callback); return () => {} } } }
        if (specifier === "@/lib/api") return api
        if (specifier === "@/lib/ai-client-scope") return loaded.scope
        if (specifier === "@/stores/ai-settings") return loaded.settings
        if (specifier === "@/stores/indicator") return { useIndicatorStore: { getState: () => ({ config: {}, applyConfig: () => calls.indicators++ }) } }
        if (specifier === "@/lib/ai-stream") return { streamChat: async (...args) => { calls.streams.push(args); return overrides.stream?.(...args) } }
        return nativeRequire(specifier)
      },
    })
    loaded[name] = module.exports
  }
  if (existsSync(new URL("../lib/ai-client-scope.ts", import.meta.url))) load("../lib/ai-client-scope.ts", "scope")
  load("./ai-settings.ts", "settings")
  load("./ai-chat.ts", "chat")
  const change = (partial) => { const before = auth; auth = { ...auth, ...partial }; subscriptions.forEach((callback) => callback(auth, before)) }
  return { chat: loaded.chat.useAIChatStore, settings: loaded.settings.useAISettingsStore, scope: loaded.scope, api, storage, calls, change, auth: () => auth }
}

test("private model caches clear synchronously and stale read/finally cannot replace a new account", async () => {
  const old = deferred(), current = deferred()
  let reads = 0
  const page = setup({ getAIModels: () => (++reads === 1 ? old.promise : current.promise) })
  page.settings.setState({ providers: [{ id: "private-a" }], models: [model()], testResults: { a: { message: "private" } } })
  const previous = page.settings.getState().fetchModels()
  page.change({ user: { id: "user-b", market_type: "futures" }, accessToken: token("user-b"), sessionRevision: 2 })
  assert.equal(page.settings.getState().models.length, 0)
  assert.equal(page.settings.getState().providers.length, 0)
  const pending = page.settings.getState().fetchModels()
  old.resolve([model("old-account")]); await previous
  assert.equal(page.settings.getState().loadingModels, true)
  assert.equal(page.settings.getState().models.length, 0)
  current.resolve([model("current-account")]); await pending
  assert.equal(page.settings.getState().models[0].id, "current-account")
})

test("a delayed write never refreshes or mutates the next login", async () => {
  const write = deferred()
  const page = setup({ createAIProvider: () => write.promise })
  const pending = page.settings.getState().addProvider({ name: "private", api_key: "fixture-not-real", api_type: "openai", base_url: "https://unused.invalid" }).catch(() => {})
  page.change({ accessToken: null, user: null, sessionRevision: 2 })
  write.resolve({}); await pending
  assert.equal(page.calls.providers, 0)
})

test("late SSE never updates another scope or its indicators and carries captured credentials", async () => {
  const page = setup()
  page.settings.setState({ models: [model()] })
  page.chat.getState().selectModel(model())
  page.chat.setState({ conversationId: "conversation-a" })
  await page.chat.getState().sendMessage("private-a")
  const old = page.calls.streams[0]
  assert.equal(old[3].accessToken, token("user-a"))
  assert.equal(old[3].modelRecordId, "row-a")
  page.change({ user: { id: "user-b", market_type: "futures" }, accessToken: token("user-b"), sessionRevision: 2 })
  assert.equal(old[3].signal.aborted, true)
  assert.equal(page.chat.getState().messages.length, 0)
  page.settings.setState({ models: [model("row-b")] })
  page.chat.getState().selectModel(model("row-b"))
  page.chat.setState({ conversationId: "conversation-b" })
  await page.chat.getState().sendMessage("private-b")
  old[2].onToolResult("update_indicator_settings", '{"success":true,"apply_config":{"rsi":{}}}', "old-call")
  old[2].onThinking("old-thinking")
  old[2].onToolCall("old-tool", "{}", "old-call")
  old[2].onConfirmationRequest("old-write", "{}", "old-confirmation")
  old[2].onReactRound(9, 10)
  old[2].onSubagentPlan([{ id: "old-agent", name: "old", status: "running" }], 1)
  old[2].onSubagentUpdate({ id: "old-agent", name: "old", status: "completed", detail: "old" })
  old[2].onError("private failure"); old[2].onDone(); old[2].onMessage("private-a-response")
  assert.equal(page.calls.indicators, 0)
  assert.equal(page.chat.getState().isStreaming, true)
  assert.equal(page.chat.getState().messages.at(-1).thinking, "")
  assert.equal(page.chat.getState().messages.at(-1).toolCalls.length, 0)
  assert.equal(page.chat.getState().messages.at(-1).confirmations.length, 0)
  assert.equal(page.chat.getState().messages.at(-1).subAgents.length, 0)
  assert.ok(!page.chat.getState().messages.some((item) => item.content.includes("private-a")))
})

test("conversation creation returning after logout cannot start a paid stream", async () => {
  const creation = deferred()
  const page = setup({ createConversation: () => creation.promise })
  page.chat.setState({ selectedModel: model() })
  const pending = page.chat.getState().sendMessage("old-prompt")
  page.change({ user: null, accessToken: null, sessionRevision: 2 })
  creation.resolve({ id: "old-created" }); await pending
  assert.equal(page.calls.streams.length, 0)
  assert.equal(page.chat.getState().conversationId, null)
})

test("conversation switches are last-intent-wins rather than last-response-wins", async () => {
  const first = deferred(), second = deferred()
  const page = setup({ getConversationMessages: (id) => id === "first" ? first.promise : second.promise })
  const older = page.chat.getState().switchConversation("first")
  const latest = page.chat.getState().switchConversation("second")
  second.resolve({ items: [], total: 0 }); await latest
  first.resolve({ items: [], total: 0 }); await older
  assert.equal(page.chat.getState().conversationId, "second")
})

test("selected model persistence is owned row identity and cannot restore an old cached list", () => {
  const page = setup()
  page.storage.set("ai:selected-model", "same-model-name")
  page.settings.setState({ models: [model()] })
  page.chat.getState().selectModel(model())
  assert.ok([...page.storage].some(([key, value]) => key.includes("user-a") && value === "row-a"))
  page.change({ user: { id: "user-b", market_type: "futures" }, accessToken: token("user-b"), sessionRevision: 2 })
  page.chat.getState().restoreModel([model()])
  assert.equal(page.chat.getState().selectedModel, null)
})

test("legacy confirmation is non-executing and never sends a control marker to the model", async () => {
  const page = setup()
  page.chat.setState({ selectedModel: model(), conversationId: "conversation-a", messages: [{ id: "bubble", role: "assistant", content: "", confirmations: [{ name: "paper_open_long", args: "{}", callId: "call", resolved: false }] }] })
  page.chat.getState().resolveConfirmation("bubble", "call", true)
  assert.equal(page.calls.streams.length, 0)
  assert.match(page.chat.getState().messages[0].content, /未执行|未开放/)
})

test("token rotation preserves a stream, but JWT mode, market and same-user relogin reset private state", async () => {
  const page = setup()
  page.chat.setState({ selectedModel: model(), conversationId: "current" })
  await page.chat.getState().sendMessage("current-prompt")
  page.change({ accessToken: token("user-a", "virtual", "rotated") })
  page.calls.streams[0][2].onMessage("still-current")
  assert.equal(page.chat.getState().messages.at(-1).content, "still-current")
  assert.equal(page.chat.getState().isStreaming, true)
  for (const patch of [{ accessToken: token("user-a", "live") }, { user: { id: "user-a", market_type: "stock" } }, { sessionRevision: 2 }]) {
    page.chat.setState({ pendingImages: ["private-image"], messages: [{ content: "private" }] })
    page.settings.setState({ models: [model()] })
    page.change(patch)
    assert.equal(page.chat.getState().messages.length, 0)
    assert.equal(page.chat.getState().pendingImages.length, 0)
    assert.equal(page.settings.getState().models.length, 0)
  }
  page.change({ accessToken: token("user-b") })
  assert.equal(page.scope.captureAiScope(page.auth()), null)
})

test("cancelling during conversation creation also cancels the pending paid send", async () => {
  const creation = deferred()
  const page = setup({ createConversation: () => creation.promise })
  page.chat.setState({ selectedModel: model() })
  const pending = page.chat.getState().sendMessage("cancel-before-create")
  page.chat.getState().cancelStream()
  creation.resolve({ id: "created-but-unused" }); await pending
  assert.equal(page.calls.streams.length, 0)
  assert.equal(page.chat.getState().isStreaming, false)
})

test("bounded conversation history appends unique rows and stale message pages never cross conversations", async () => {
  const older = deferred()
  const offsets = []
  const page = setup({ getConversations: async (paging) => {
    offsets.push(paging.offset)
    return paging.offset === 0 ? Array.from({ length: 50 }, (_, index) => ({ id: String(index) })) : [{ id: "49" }, { id: "50" }]
  }, getConversationMessages: (id) => id === "old" ? older.promise : Promise.resolve({ items: [], total: 0 }) })
  await page.chat.getState().loadConversations()
  assert.equal(page.chat.getState().hasMoreConversations, true)
  await page.chat.getState().loadMoreConversations()
  assert.equal(page.chat.getState().conversations.length, 51)
  assert.deepEqual(offsets, [0, 50])
  page.chat.setState({ conversationId: "old", hasMoreMessages: true })
  const pending = page.chat.getState().loadMoreMessages()
  await page.chat.getState().switchConversation("new")
  older.resolve({ items: [{ id: "old-message", role: "user", content: "private-old", created_at: "2026-01-01" }], total: 100 }); await pending
  assert.equal(page.chat.getState().conversationId, "new")
  assert.equal(page.chat.getState().messages.length, 0)
})

test("pending market confirmation invalidates writes even when the market returns to the original scope", async () => {
  const write = deferred()
  const page = setup({ createAIProvider: () => write.promise })
  page.chat.setState({ selectedModel: model(), conversationId: "existing" })
  await page.chat.getState().sendMessage("old-market")
  const old = page.calls.streams[0]
  const pending = page.settings.getState().addProvider({ name: "fixture", api_key: "synthetic", api_type: "openai", base_url: "https://unused.invalid" }).catch(() => {})
  page.change({ marketSwitchPending: true })
  assert.equal(old[3].signal.aborted, true)
  assert.equal(page.scope.captureAiScope(page.auth()), null)
  page.change({ marketSwitchPending: false })
  write.resolve({}); await pending
  assert.equal(page.calls.providers, 0)
})

test("late conversation deletion never switches a re-entered market scope", async () => {
  const deletion = deferred()
  const page = setup({ deleteConversation: () => deletion.promise })
  page.chat.setState({ conversationId: "old", conversations: [{ id: "old" }, { id: "other" }] })
  const pending = page.chat.getState().removeConversation("old")
  page.change({ marketSwitchPending: true }); page.change({ marketSwitchPending: false })
  page.chat.setState({ conversationId: "old", conversations: [{ id: "old" }, { id: "new" }] })
  deletion.resolve({}); await pending
  assert.equal(page.chat.getState().conversationId, "old")
  assert.equal(page.chat.getState().conversations.length, 2)
})

test("older pages preserve tool records and unsupported legacy objects are visible instead of crashing", async () => {
  const item = { id: "history-item", role: "assistant", content: "saved-answer", created_at: "2026-01-01", tool_calls: [{ name: "query", args: "{}", call_id: "saved" }], tool_result: [{ name: "query", content: "saved-result", call_id: "saved" }] }
  const page = setup({ getConversationMessages: async (_id, index) => ({ items: [index === 1 ? { ...item, id: "recent", tool_calls: { unsupported_legacy: true }, tool_result: null } : item], total: 51 }) })
  await page.chat.getState().switchConversation("history")
  assert.match(page.chat.getState().messages[0].error ?? "", /工具记录/)
  await page.chat.getState().loadMoreMessages()
  const oldest = page.chat.getState().messages[0]
  assert.equal(oldest.toolCalls[0].callId, "saved")
  assert.equal(oldest.toolResults[0].content, "saved-result")
})

test("deleting the selected model aborts its stream and cannot restore a same-name stale row", async () => {
  const page = setup()
  page.settings.setState({ models: [model(), model("row-b")] })
  page.chat.getState().selectModel(model())
  page.chat.setState({ conversationId: "existing" })
  await page.chat.getState().sendMessage("fixture")
  await page.settings.getState().removeModel("row-a")
  assert.equal(page.calls.streams[0][3].signal.aborted, true)
  assert.equal(page.chat.getState().selectedModel, null)
  page.chat.getState().selectModel(model())
  assert.equal(page.chat.getState().selectedModel, null)
})

test("disabled providers cannot be selected or restored and their old stream is stopped", async () => {
  const page = setup()
  page.settings.setState({ models: [model()], providers: [{ id: "provider", is_active: true }] })
  page.chat.getState().selectModel(model())
  page.chat.setState({ conversationId: "existing" })
  await page.chat.getState().sendMessage("fixture")
  page.settings.setState({ providers: [{ id: "provider", is_active: false }] })
  assert.equal(page.calls.streams[0][3].signal.aborted, true)
  page.chat.getState().restoreModel([model()])
  assert.equal(page.chat.getState().selectedModel, null)
})

test("a new identity never inherits another users cooldown or analysis mode", () => {
  const page = setup()
  page.chat.setState({ chatMode: "legacy", cooldownUntil: Date.now() + 60000 })
  page.change({ user: { id: "user-b", market_type: "futures" }, accessToken: token("user-b"), sessionRevision: 2 })
  assert.equal(page.chat.getState().chatMode, "plain")
  assert.equal(page.chat.getState().cooldownUntil, 0)
})

test("plain mode does not send multimodal content to the provider", async () => {
  const page = setup()
  page.chat.setState({ selectedModel: model(), conversationId: "fixture", pendingImages: ["data:image/png;base64,fixture"] })
  await page.chat.getState().sendMessage("image")
  assert.equal(page.calls.streams.length, 0)
})
