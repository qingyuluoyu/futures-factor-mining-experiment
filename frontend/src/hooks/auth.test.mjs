import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"

const requireModule = createRequire(import.meta.url)
const output = ts.transpileModule(readFileSync(new URL("./auth.ts", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText

function guard(pathname, loaded) {
  const redirects = [], effects = []
  const noop = () => {}
  const auth = { accessToken: null, user: null, loaded, login: noop, logout: noop, setLoaded: noop, setHydrating: noop, restoreSessionToken: noop }
  const store = (selector) => selector(auth)
  store.getState = () => auth
  const dependencies = {
    react: { useEffect: (callback) => effects.push(callback) },
    "next/navigation": { usePathname: () => pathname, useRouter: () => ({ replace: (path) => redirects.push(path) }) },
    "@/stores/auth": { useAuthStore: store },
    "@/lib/api": { getBoundSessionApi: () => assert.fail("Signed out must not fetch a session") },
    "@/lib/auth-session": { loadSessionOnce: () => assert.fail("Signed out cannot restore a token"), sessionStillCurrent: () => false },
  }
  const module = { exports: {} }
  vm.runInNewContext(output, { module, exports: module.exports, localStorage: { getItem: () => null },
    require: (name) => dependencies[name] ?? requireModule(name) })
  module.exports.useAuthGuard()
  effects.forEach((effect) => effect())
  return redirects
}

test("signed-out research entry routes redirect after hydration instead of waiting forever", () => {
  for (const path of ["/factor-lab", "/factor-research", "/backtest", "/market/screener"]) assert.deepEqual(guard(path, true), ["/login"])
})

test("research routes do not redirect during hydration and public login stays public", () => {
  assert.deepEqual(guard("/factor-lab", false), [])
  assert.deepEqual(guard("/factor-research", false), [])
  assert.deepEqual(guard("/login", true), [])
})
