import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"

test("main layout refreshes futures finances on an authenticated scope change but never in stock or signed-out scope", () => {
  const source = readFileSync(new URL("./financial-session-sync.tsx", import.meta.url), "utf8")
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const calls = [], effects = []
  let identity = { loaded: true, accessToken: "fixture-token", sessionRevision: 1, user: { id: "one", market_type: "futures", trading_mode: "virtual" } }
  const module = { exports: {} }
  const refresh = () => calls.push(identity.accessToken)
  vm.runInNewContext(output, { module, exports: module.exports, require: (name) => ({
    react: { useEffect: (callback, dependencies) => effects.push({ callback, dependencies }) },
    "@/stores/auth": { useAuthStore: (selector) => selector(identity) },
    "@/stores/paper-trading": { usePaperTradingStore: (selector) => selector({ refresh }) },
  })[name] })
  module.exports.FinancialSessionSync(); effects.pop().callback()
  assert.deepEqual(calls, ["fixture-token"])
  for (const patch of [{ marketSwitchPending: true }, { loaded: false }, { accessToken: null }, { user: { id: "one", market_type: "stock" } }]) {
    const original = identity; identity = { ...identity, ...patch }
    module.exports.FinancialSessionSync(); effects.pop().callback(); identity = original
  }
  assert.equal(calls.length, 1)
  identity = { ...identity, marketSwitchPending: true }
  module.exports.FinancialSessionSync(); const pendingEffect = effects.pop(); pendingEffect.callback()
  identity = { ...identity, marketSwitchPending: false }
  module.exports.FinancialSessionSync(); const confirmedEffect = effects.pop(); confirmedEffect.callback()
  assert.notDeepEqual(confirmedEffect.dependencies, pendingEffect.dependencies)
  assert.equal(calls.length, 2)
  identity = { ...identity, sessionRevision: 2, accessToken: "new-token" }
  module.exports.FinancialSessionSync(); const effect = effects.pop(); effect.callback()
  assert.ok(effect.dependencies.includes(2) && effect.dependencies.includes("new-token"))
  assert.deepEqual(calls, ["fixture-token", "fixture-token", "new-token"])
  const layout = readFileSync(new URL("../../app/(main)/layout.tsx", import.meta.url), "utf8")
  assert.match(layout, /<FinancialSessionSync\s*\/>/)
})
