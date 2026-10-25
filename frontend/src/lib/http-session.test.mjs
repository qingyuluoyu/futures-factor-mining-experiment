import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"

function deferred() {
  let resolve, reject
  const promise = new Promise((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

function response(status, payload = {}) {
  return { status, ok: status >= 200 && status < 300, json: async () => payload }
}

function fixture() {
  const storage = new Map(), calls = [], modules = new Map()
  const location = { href: "" }
  const context = vm.createContext({
    window: { location }, process: { env: {} }, AbortSignal,
    localStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, String(value)),
      removeItem: (key) => storage.delete(key),
    },
    fetch: (url, options) => {
      const result = deferred()
      calls.push({ url, options, ...result })
      return result.promise
    },
  })
  const dependencies = {
    zustand: { create: (initialize) => {
      let state
      const getState = () => state
      state = initialize((patch) => { state = { ...state, ...patch } }, getState)
      return { getState }
    } },
    "@/lib/websocket": { getMarketWebSocket: () => ({ disconnect() {} }) },
    "@/stores/notifications": { useNotificationsStore: { getState: () => ({ reset() {} }) } },
  }
  function load(url) {
    const path = fileURLToPath(url)
    if (modules.has(path)) return modules.get(path).exports
    const module = { exports: {} }
    modules.set(path, module)
    const output = ts.transpileModule(readFileSync(url, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText
    const require = (name) => {
      if (name in dependencies) return dependencies[name]
      const resolved = name.startsWith("@/") ? new URL(`../${name.slice(2)}.ts`, import.meta.url) : new URL(name.endsWith(".ts") ? name : `${name}.ts`, url)
      return load(resolved)
    }
    vm.runInContext(`(function(require, module, exports) { ${output}\n})`, context, { filename: path })(require, module, module.exports)
    return module.exports
  }
  const api = load(new URL("./api.ts", import.meta.url))
  const store = load(new URL("../stores/auth.ts", import.meta.url)).useAuthStore
  const login = (name = "a", access = `access-${name}`, refresh = `refresh-${name}`) => store.getState().login({ id: `user-${name}`, username: name }, access, refresh)
  login()
  return { api, store, login, storage, calls, location }
}

async function waitFor(predicate) {
  for (let attempt = 0; attempt < 30; attempt++) {
    if (predicate()) return
    await Promise.resolve()
  }
  assert.ok(predicate(), "expected asynchronous boundary was not reached")
}

async function settleUnexpected(page, start) {
  for (let attempt = 0; attempt < 30; attempt++) {
    await Promise.resolve()
    for (const call of page.calls.slice(start)) call.resolve(response(401))
  }
}

test("an old 401 cannot refresh the new login or replay a write", async () => {
  const page = fixture()
  const pending = page.api.request("/protected-write", { method: "POST", body: "{}" })
  const rejected = assert.rejects(pending, /会话已变更/)
  page.login("b")
  page.calls[0].resolve(response(401))
  await settleUnexpected(page, 1)
  await rejected
  assert.equal(page.calls.length, 1)
  assert.equal(page.storage.get("access_token"), "access-b")
  assert.equal(page.location.href, "")
})

for (const success of [true, false]) test(`late refresh ${success ? "success" : "failure"} cannot overwrite or logout a new login`, async () => {
  const page = fixture()
  const pending = page.api.request("/protected-write", { method: "POST" })
  const rejected = assert.rejects(pending, /会话已变更/)
  page.calls[0].resolve(response(401))
  await waitFor(() => page.calls.length === 2)
  assert.equal(JSON.parse(page.calls[1].options.body).refresh_token, "refresh-a")
  page.login("b")
  page.calls[1].resolve(response(success ? 200 : 401, { access_token: "late-access-a", refresh_token: "late-refresh-a" }))
  await settleUnexpected(page, 2)
  await rejected
  assert.equal(page.calls.length, 2)
  assert.equal(page.store.getState().accessToken, "access-b")
  assert.equal(page.storage.get("refresh_token"), "refresh-b")
  assert.equal(page.location.href, "")
})

test("a second login with identical tokens invalidates the old request revision", async () => {
  const page = fixture()
  const pending = page.api.request("/protected-write", { method: "POST" })
  const rejected = assert.rejects(pending, /会话已变更/)
  page.login()
  page.calls[0].resolve(response(401))
  await settleUnexpected(page, 1)
  await rejected
  assert.equal(page.calls.length, 1)
  assert.equal(page.store.getState().user.id, "user-a")
  assert.equal(page.location.href, "")
})

test("concurrent HTTP failures and WebSocket refresh share one refresh and its returned tokens", async () => {
  const page = fixture()
  const first = page.api.request("/one")
  const second = page.api.request("/two")
  page.calls[0].resolve(response(401))
  page.calls[1].resolve(response(401))
  await waitFor(() => page.calls.length >= 3)
  const websocket = page.api.tryRefreshToken()
  await Promise.resolve()
  const refreshes = page.calls.filter((call) => call.url.endsWith("/api/auth/refresh"))
  const wasSingleFlight = refreshes.length === 1
  for (const call of refreshes) call.resolve(response(200, { access_token: "rotated-a", refresh_token: "rotated-refresh-a" }))
  await waitFor(() => page.calls.filter((call) => !call.url.endsWith("/api/auth/refresh")).length === 4)
  for (const call of page.calls.slice(2).filter((call) => !call.url.endsWith("/api/auth/refresh"))) call.resolve(response(200, { ok: true }))
  await Promise.all([first, second, websocket])
  assert.equal(wasSingleFlight, true, "same session must consume one refresh call")
  for (const call of page.calls.slice(3)) assert.equal(call.options.headers.Authorization, "Bearer rotated-a")
  assert.equal(page.store.getState().accessToken, "rotated-a")
  assert.equal(page.storage.get("refresh_token"), "rotated-refresh-a")
})

test("a delayed same-session 401 reuses the completed refresh instead of refreshing again", async () => {
  const page = fixture()
  const first = page.api.request("/one")
  const second = page.api.request("/two")
  page.calls[0].resolve(response(401))
  await waitFor(() => page.calls.length === 3)
  page.calls[2].resolve(response(200, { access_token: "rotated-a", refresh_token: "rotated-refresh-a" }))
  await waitFor(() => page.calls.length === 4)
  page.calls[3].resolve(response(200))
  await first
  page.calls[1].resolve(response(401))
  await waitFor(() => page.calls.length === 5)
  const delayed = page.calls[4]
  const reused = !delayed.url.endsWith("/api/auth/refresh")
  delayed.resolve(response(200, reused ? {} : { access_token: "rotated-twice" }))
  if (!reused) {
    await waitFor(() => page.calls.length === 6)
    page.calls[5].resolve(response(200))
  }
  await second
  assert.equal(reused, true)
  assert.equal(delayed.options.headers.Authorization, "Bearer rotated-a")
})

for (const phase of ["response", "body"]) test(`stale success during ${phase} is not returned to a new user`, async () => {
  const page = fixture()
  const pending = page.api.request("/private-data")
  const rejected = assert.rejects(pending, /会话已变更/)
  if (phase === "body") {
    const body = deferred()
    let parsing = false
    page.calls[0].resolve({ ...response(200), json: () => { parsing = true; return body.promise } })
    await waitFor(() => parsing)
    page.login("b")
    body.resolve({ private: "a" })
  } else {
    page.login("b")
    page.calls[0].resolve(response(200, { private: "a" }))
  }
  await rejected
})

test("logout while refreshing cannot resurrect tokens", async () => {
  const page = fixture()
  const pending = page.api.tryRefreshToken()
  await waitFor(() => page.calls.length === 1)
  page.store.getState().logout()
  page.calls[0].resolve(response(200, { access_token: "late-a", refresh_token: "late-refresh-a" }))
  assert.equal(await pending, false)
  assert.equal(page.storage.has("access_token"), false)
  assert.equal(page.store.getState().user, null)
})

test("same tokens with a new revision reject an in-flight refresh result", async () => {
  const page = fixture()
  const pending = page.api.tryRefreshToken()
  await waitFor(() => page.calls.length === 1)
  page.login()
  page.calls[0].resolve(response(200, { access_token: "late-a" }))
  assert.equal(await pending, false)
  assert.equal(page.store.getState().accessToken, "access-a")
})

test("another tab replacing stored tokens invalidates an old refresh", async () => {
  const page = fixture()
  const pending = page.api.tryRefreshToken()
  await waitFor(() => page.calls.length === 1)
  page.storage.set("access_token", "other-tab-access")
  page.storage.set("refresh_token", "other-tab-refresh")
  page.calls[0].resolve(response(200, { access_token: "late-a" }))
  assert.equal(await pending, false)
  assert.equal(page.storage.get("access_token"), "other-tab-access")
})

test("an expired current session clears the auth store and redirects once", async () => {
  const page = fixture()
  const pending = page.api.request("/private-data")
  const rejected = assert.rejects(pending, /认证过期/)
  page.calls[0].resolve(response(401))
  await waitFor(() => page.calls.length === 2)
  page.calls[1].resolve(response(401))
  await rejected
  assert.equal(page.location.href, "/login")
  assert.equal(page.store.getState().user, null)
  assert.equal(page.storage.has("refresh_token"), false)
})

test("a refreshed DELETE preserves the 204 no-content contract", async () => {
  const page = fixture()
  const pending = page.api.request("/resource", { method: "DELETE" })
  page.calls[0].resolve(response(401))
  await waitFor(() => page.calls.length === 2)
  page.calls[1].resolve(response(200, { access_token: "rotated-a" }))
  await waitFor(() => page.calls.length === 3)
  page.calls[2].resolve({ ...response(204), json: async () => { throw new Error("empty body") } })
  assert.equal(await pending, undefined)
})

test("an invalid refresh token response never writes tokens", async () => {
  const page = fixture()
  const pending = page.api.tryRefreshToken()
  await waitFor(() => page.calls.length === 1)
  page.calls[0].resolve(response(200, { access_token: { forged: true } }))
  assert.equal(await pending, false)
  assert.equal(page.storage.get("access_token"), "access-a")
})
