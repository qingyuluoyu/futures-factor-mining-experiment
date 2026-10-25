import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import { renderToStaticMarkup } from "react-dom/server"
import { jsx } from "react/jsx-runtime"
import ts from "typescript"

const requireModule = createRequire(import.meta.url)
function brandModule(path, dependencies = {}) {
  const source = readFileSync(new URL(path, import.meta.url), "utf8")
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const module = { exports: {} }
  vm.runInNewContext(output, { module, exports: module.exports, require: (name) => dependencies[name] ?? requireModule(name) })
  return module.exports
}
const brand = brandModule("../../../lib/brand.ts")
const logo = brandModule("../../common/brand-logo.tsx", { "@/lib/brand": brand })
const formSources = [
  ["registration page", new URL("../../../app/(auth)/register/page.tsx", import.meta.url), "default"],
  ["landing registration", new URL("./auth-register-form.tsx", import.meta.url), "AuthRegisterForm"],
]

function loadForm(sourceUrl, exportName, status, sessionFailure = false) {
  const source = readFileSync(sourceUrl, "utf8")
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText
  const calls = { registrations: [], errors: [], redirects: [], storage: [], storeUpdates: [], logins: [], handshakes: [] }
  const login = (...credentials) => calls.logins.push(credentials)
  const useAuthStore = (selector) => selector ? selector({ login }) : { login }
  useAuthStore.setState = (update) => calls.storeUpdates.push(update)
  const react = {
    useEffect: () => {},
    useState: (initial) => [
      initial === null ? status : initial === true ? false : typeof initial === "object"
        ? { ...initial, username: "new_trader", password: "secret12", confirmPassword: "secret12" }
        : initial,
      (value) => { if (typeof value === "string") calls.errors.push(value) },
    ],
  }
  const dependencies = {
    react,
    "@/lib/brand": brand,
    "@/components/common/brand-logo": logo,
    "next/navigation": { useRouter: () => ({ push: (path) => calls.redirects.push(path) }) },
    "next/link": { default: (props) => jsx("a", props) },
    "@/stores/auth": { useAuthStore },
    "@/lib/api": {
      registerApi: async (payload) => {
        calls.registrations.push(payload)
        return { access_token: "test-access", refresh_token: "test-refresh" }
      },
      getMeApi: async () => ({ id: "qa-user", username: "new_trader", role: "user" }),
      getBoundSessionApi: async (...tokens) => {
        calls.handshakes.push(tokens)
        assert.equal(calls.storage.length, 0)
        assert.equal(calls.storeUpdates.length, 0)
        assert.equal(calls.logins.length, 0)
        if (sessionFailure) throw new Error("session unavailable")
        return {
          user: { id: "qa-user", username: "new_trader", role: "user" },
          accessToken: "bound-access",
          refreshToken: "bound-refresh",
        }
      },
    },
    "@/lib/admin-api": { getRegistrationStatusApi: async () => status },
    "@/components/ui/input": { Input: (props) => jsx("input", props) },
    "@/components/ui/label": { Label: (props) => jsx("label", props) },
    "@/components/ui/button": { Button: (props) => jsx("button", props) },
  }
  const module = { exports: {} }
  vm.runInNewContext(output, {
    module,
    exports: module.exports,
    require: (name) => dependencies[name] ?? requireModule(name),
    localStorage: { setItem: (...values) => calls.storage.push(values) },
    TextEncoder,
  })
  const tree = module.exports[exportName]({ onSuccess: () => {}, onSwitchLogin: () => {} })
  return { tree, calls }
}

function findElement(tree, predicate) {
  if (!tree || typeof tree !== "object") return undefined
  if (predicate(tree)) return tree
  const children = tree.props?.children
  for (const child of Array.isArray(children) ? children : [children]) {
    if (Array.isArray(child)) {
      for (const nested of child) {
        const match = findElement(nested, predicate)
        if (match) return match
      }
    } else {
      const match = findElement(child, predicate)
      if (match) return match
    }
  }
  return undefined
}

const openStatus = {
  registration_policy: "open",
  registration_enabled: true,
  daily_register_limit: 0,
  register_reset_hour: 6,
  registered_in_window: 200,
  remaining: null,
  can_register: true,
  reason: "",
}

for (const [label, sourceUrl, exportName] of formSources) {
  test(`${label}: username and password submit without agreement or contact details`, async () => {
    const { tree, calls } = loadForm(sourceUrl, exportName, openStatus)
    const form = findElement(tree, (element) => element.type === "form")
    await form.props.onSubmit({ preventDefault: () => {} })
    assert.equal(calls.registrations.length, 1)
    assert.equal(calls.registrations[0].username, "new_trader")
    assert.equal(calls.registrations[0].phone, null)
    assert.deepEqual(calls.errors.filter(Boolean), [])
    assert.deepEqual(calls.redirects, ["/dashboard"])
  })

  test(`${label}: identity changes only after the bound session handshake`, async () => {
    const { tree, calls } = loadForm(sourceUrl, exportName, openStatus)
    const form = findElement(tree, (element) => element.type === "form")
    await form.props.onSubmit({ preventDefault: () => {} })
    assert.deepEqual(calls.handshakes, [["test-access", "test-refresh"]])
    assert.equal(calls.logins.length, 1)
    assert.equal(calls.logins[0][1], "bound-access")
    assert.equal(calls.logins[0][2], "bound-refresh")
    assert.deepEqual(calls.storage, [])
    assert.deepEqual(calls.storeUpdates, [])
  })

  test(`${label}: failed session handshake tells the created user to sign in`, async () => {
    const { tree, calls } = loadForm(sourceUrl, exportName, openStatus, true)
    const form = findElement(tree, (element) => element.type === "form")
    await form.props.onSubmit({ preventDefault: () => {} })
    assert.equal(calls.registrations.length, 1)
    assert.equal(calls.handshakes.length, 1)
    assert.deepEqual(calls.logins, [])
    assert.deepEqual(calls.storage, [])
    assert.deepEqual(calls.storeUpdates, [])
    assert.deepEqual(calls.redirects, [])
    assert.ok(calls.errors.some((message) => /账号已创建.*登录/.test(message)))
  })

  test(`${label}: open policy has no quota or nonexistent terms gate`, () => {
    const { tree } = loadForm(sourceUrl, exportName, openStatus)
    const markup = renderToStaticMarkup(tree)
    assert.doesNotMatch(markup, /用户协议|隐私政策|type="checkbox"|今日剩余|重置/)
    assert.match(markup, /无每日名额限制/)
    const phone = findElement(tree, (element) => element.props?.id?.endsWith("phone"))
    assert.ok(phone)
    assert.notEqual(phone.props.required, true)
    const username = findElement(tree, (element) => element.props?.id?.endsWith("username"))
    assert.equal(username.props.maxLength, 50)
  })

  test(`${label}: managed closure still blocks submit`, async () => {
    const { tree, calls } = loadForm(sourceUrl, exportName, {
      ...openStatus, registration_policy: "managed", can_register: false, reason: "系统已关闭注册",
    })
    const form = findElement(tree, (element) => element.type === "form")
    await form.props.onSubmit({ preventDefault: () => {} })
    assert.equal(calls.registrations.length, 0)
    assert.ok(calls.errors.includes("系统已关闭注册"))
  })
}
