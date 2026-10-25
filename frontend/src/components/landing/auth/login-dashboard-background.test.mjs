import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import { renderToStaticMarkup } from "react-dom/server"
import { jsx } from "react/jsx-runtime"
import ts from "typescript"
import { loadDashboardFrame } from "../../dashboard/dashboard-frame-harness.mjs"

const requireModule = createRequire(import.meta.url)
const target = new URL("./login-dashboard-background.tsx", import.meta.url)
const collectedAt = "2026-10-21T03:00:00+00:00"
const emptyPayload = { updated_at: collectedAt, tasks: [], running_count: 0 }

function loadBackground({ fetch = async () => ({ ok: true, json: async () => emptyPayload }), state, timers = {} } = {}) {
  const output = ts.transpileModule(existsSync(target) ? readFileSync(target, "utf8") : "", {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText
  const module = { exports: {} }
  const effects = []
  const states = []
  vm.runInNewContext(output, {
    module, exports: module.exports, AbortController, URLSearchParams, process: { env: {} },
    fetch, setTimeout: timers.setTimeout ?? setTimeout, clearTimeout: timers.clearTimeout ?? clearTimeout,
    require: (name) => name === "react" ? {
      useState: (initial) => [state ?? initial, (next) => states.push(next)],
      useEffect: (effect) => effects.push(effect),
    } : name === "@/lib/brand" ? { BRAND_NAME: "闻苑" } : name === "@/components/dashboard/dashboard-frame" ? loadDashboardFrame() : requireModule(name),
  })
  return { ...module.exports, effects, states }
}

test("login preview reads only the bounded public endpoint without credentials", async () => {
  const requests = []
  const page = loadBackground({ fetch: async (url, options) => {
    requests.push({ url, options })
    return { ok: true, json: async () => ({ ...emptyPayload, total_pnl: 0, user_id: "ignored", equity_series: { ignored: [] } }) }
  } })
  assert.equal(typeof page.fetchLoginDashboardSummary, "function", "bounded public summary loader is missing")
  const summary = await page.fetchLoginDashboardSummary()
  assert.equal(requests.length, 1)
  assert.equal(requests[0].url, "/api/showcase/ai-trading?limit=4")
  assert.equal(requests[0].options.credentials, "omit")
  assert.equal(requests[0].options.cache, "no-store")
  assert.equal(requests[0].options.headers, undefined)
  assert.equal(requests[0].options.signal.aborted, false)
  assert.deepEqual(JSON.parse(JSON.stringify(summary)), { collectedAt, taskCount: 0, runningCount: 0, pausedCount: 0 })
})

test("summary retains real running and paused zeroes while discarding financial and identity fields", async () => {
  const page = loadBackground({ fetch: async () => ({ ok: true, json: async () => ({
    ...emptyPayload, running_count: 1, total_pnl: -123.45,
    tasks: [{ status: "running", user_id: "ignored", allocated_capital: 100 }, { status: "paused" }],
  }) }) })
  assert.equal(typeof page.fetchLoginDashboardSummary, "function", "public summary loader is missing")
  const summary = await page.fetchLoginDashboardSummary()
  assert.deepEqual(JSON.parse(JSON.stringify(summary)), { collectedAt, taskCount: 2, runningCount: 1, pausedCount: 1 })
})

for (const payload of [
  null,
  { ...emptyPayload, tasks: null },
  { ...emptyPayload, updated_at: "unknown" },
  { ...emptyPayload, updated_at: "2026-10-21T03:00:00" },
  { ...emptyPayload, running_count: null },
  { ...emptyPayload, running_count: 1 },
  { ...emptyPayload, tasks: [{ status: "unknown" }] },
  { ...emptyPayload, tasks: Array.from({ length: 5 }, () => ({ status: "paused" })) },
]) test(`malformed public data is unavailable rather than zero-filled: ${JSON.stringify(payload)}`, async () => {
  const page = loadBackground({ fetch: async () => ({ ok: true, json: async () => payload }) })
  assert.equal(typeof page.fetchLoginDashboardSummary, "function", "public summary loader is missing")
  await assert.rejects(page.fetchLoginDashboardSummary(), /公开数据格式异常/)
})

test("HTTP failures are explicit and never expose response bodies or retry", async () => {
  let calls = 0
  const page = loadBackground({ fetch: async () => {
    calls++
    return { ok: false, status: 503, json: async () => { throw new Error("private response must not be read") } }
  } })
  assert.equal(typeof page.fetchLoginDashboardSummary, "function", "public summary loader is missing")
  await assert.rejects(page.fetchLoginDashboardSummary(), /公开数据暂不可用.*503/)
  assert.equal(calls, 1)
})

test("invalid JSON is a data parsing failure rather than a network failure", async () => {
  const page = loadBackground({ fetch: async () => ({ ok: true, json: async () => { throw new SyntaxError("server body") } }) })
  await assert.rejects(page.fetchLoginDashboardSummary(), /公开数据格式异常/)
})

test("an already cancelled request never accesses the public service", async () => {
  let requests = 0
  const page = loadBackground({ fetch: async () => { requests++; throw new Error("not expected") } })
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(page.fetchLoginDashboardSummary(controller.signal), /取消/)
  assert.equal(requests, 0)
})

test("network errors render a safe public failure without disclosing the thrown message", async () => {
  const page = loadBackground({ fetch: async () => { throw new Error("private upstream diagnostics") } })
  await assert.rejects(page.fetchLoginDashboardSummary(), /公开数据连接失败/)
  page.LoginDashboardBackground()
  const cleanup = page.effects[0]()
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(page.states[0].status, "failed")
  assert.equal(page.states[0].message, "公开数据连接失败")
  cleanup()
})

test("a successful public request updates the preview to the genuine empty snapshot", async () => {
  const page = loadBackground()
  page.LoginDashboardBackground()
  const cleanup = page.effects[0]()
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(page.states[0].status, "ready")
  assert.equal(page.states[0].summary.taskCount, 0)
  assert.equal(page.states[0].summary.collectedAt, collectedAt)
  cleanup()
})

test("request has an eight second deadline and cancels its network operation", async () => {
  let expire, timeoutMs, requestSignal, cleared = false
  const page = loadBackground({
    timers: { setTimeout: (callback, milliseconds) => { expire = callback; timeoutMs = milliseconds; return 7 }, clearTimeout: (id) => { cleared = id === 7 } },
    fetch: async (url, { signal }) => {
      requestSignal = signal
      return new Promise((resolve, reject) => signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true }))
    },
  })
  assert.equal(typeof page.fetchLoginDashboardSummary, "function", "public summary loader is missing")
  const pending = page.fetchLoginDashboardSummary()
  assert.equal(timeoutMs, 8000)
  expire()
  await assert.rejects(pending, /公开数据请求超时/)
  assert.equal(requestSignal.aborted, true)
  assert.equal(cleared, true)
})

test("unmount aborts the background request without updating state after cancellation", async () => {
  let requestSignal
  const page = loadBackground({ fetch: async (url, { signal }) => {
    requestSignal = signal
    return new Promise((resolve, reject) => signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true }))
  } })
  assert.equal(typeof page.LoginDashboardBackground, "function", "public dashboard background is missing")
  page.LoginDashboardBackground()
  assert.equal(page.effects.length, 1)
  const cleanup = page.effects[0]()
  cleanup()
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(requestSignal.aborted, true)
  assert.deepEqual(page.states, [])
})

test("empty preview is an inert dashboard with honest zeroes and collection time", () => {
  const page = loadBackground({ state: { status: "ready", summary: { collectedAt, taskCount: 0, runningCount: 0, pausedCount: 0 } } })
  assert.equal(typeof page.LoginDashboardBackground, "function", "public dashboard background is missing")
  const markup = renderToStaticMarkup(page.LoginDashboardBackground())
  assert.match(markup, /inert=""/)
  assert.match(markup, /aria-hidden="true"/)
  assert.match(markup, /blur-\[1\.5px\]/)
  assert.match(markup, /暂无公开运行数据/)
  assert.match(markup, /2026-10-21/)
  assert.match(markup, /采集时间/)
  assert.match(markup, /公开样本/)
  assert.match(markup, />0</)
  assert.doesNotMatch(markup, /<button|<input|<a\s|<canvas|<svg|NaN|undefined/)
})

test("loading and failure never turn into fabricated metrics", () => {
  for (const state of [{ status: "loading" }, { status: "failed", message: "公开数据请求超时" }]) {
    const page = loadBackground({ state })
    assert.equal(typeof page.LoginDashboardBackground, "function", "public dashboard background is missing")
    const markup = renderToStaticMarkup(page.LoginDashboardBackground())
    assert.doesNotMatch(markup, />0</)
    assert.match(markup, state.status === "loading" ? /正在读取公开数据/ : /公开数据请求超时/)
    assert.match(markup, /采集时间.*未取得/s)
  }
})

test("login preserves credential fields, bound session flow and its original trading selection", async () => {
  const source = readFileSync(new URL("../../../app/(auth)/login/page.tsx", import.meta.url), "utf8")
  const calls = { login: [], sessions: [], remembered: [], redirects: [] }
  const values = ["qa-user", "qa-password", "virtual", true, "", false, true]
  let stateIndex = 0
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const module = { exports: {} }
  const dependencies = {
    react: { useEffect: () => {}, useState: () => [values[stateIndex++], () => {}] },
    "next/navigation": { useRouter: () => ({ push: (path) => calls.redirects.push(path) }) },
    "next/link": { default: (props) => jsx("a", props) },
    "@/stores/auth": { useAuthStore: { getState: () => ({ sessionRevision: 42 }) } },
    "@/lib/api": { loginApi: async (input) => { calls.login.push(input); return { access_token: "bound" } } },
    "@/lib/login-session": { establishLoginSession: async (...args) => { calls.sessions.push(args); return true } },
    "@/lib/remembered-account": { loadRememberedAccount: () => ({}), saveRememberedAccount: (...args) => calls.remembered.push(args) },
    "@/components/ui/button": { Button: (props) => jsx("button", props) },
    "@/components/ui/input": { Input: (props) => jsx("input", props) },
    "@/components/ui/label": { Label: (props) => jsx("label", props) },
    "@/components/common/brand-logo": { BrandLogo: () => null },
    "@/lib/brand": { BRAND_NAME: "闻苑", BRAND_TAGLINE: "test" },
    "@/components/landing/auth/login-dashboard-background": { LoginDashboardBackground: () => null },
  }
  vm.runInNewContext(output, { module, exports: module.exports, require: (name) => dependencies[name] ?? requireModule(name) })
  const tree = module.exports.default()
  const find = (element, predicate) => {
    if (!element || typeof element !== "object") return undefined
    if (predicate(element)) return element
    for (const child of [element.props?.children].flat(Infinity)) {
      const result = find(child, predicate)
      if (result) return result
    }
  }
  assert.ok(find(tree, (element) => element.type === dependencies["@/components/landing/auth/login-dashboard-background"].LoginDashboardBackground), "login must render the public background")
  assert.match(tree.props.className, /fixed.*overflow-hidden/)
  assert.equal(find(tree, (element) => element.props?.id === "username").props.autoComplete, "username")
  assert.equal(find(tree, (element) => element.props?.id === "password").props.autoComplete, "current-password")
  await find(tree, (element) => element.type === "form").props.onSubmit({ preventDefault: () => {} })
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), {
    login: [{ account: "qa-user", password: "qa-password", trading_mode: "virtual" }],
    sessions: [[{ access_token: "bound" }, 42]], remembered: [[true, "qa-user"]], redirects: ["/dashboard"],
  })
})
