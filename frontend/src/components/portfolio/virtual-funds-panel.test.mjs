import assert from "node:assert/strict"
import test from "node:test"
import { readFile } from "node:fs/promises"
import vm from "node:vm"
import { createRequire } from "node:module"
import ts from "typescript"

test("funding panel remounts on same-token relogin and refuses the stock market", async () => {
  const source = await readFile(new URL("./virtual-funds-panel.tsx", import.meta.url), "utf8")
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  let identity = { accessToken: "fixture-token", sessionRevision: 1, user: { id: "fixture-user", market_type: "futures", trading_mode: "virtual" } }
  const module = { exports: {} }, requireModule = createRequire(import.meta.url)
  vm.runInNewContext(output, { module, exports: module.exports, require: (name) => name === "@/stores/auth" ? { useAuthStore: (selector) => selector(identity) } : name.startsWith("@/") ? {} : requireModule(name) })
  const first = module.exports.VirtualFundsPanel()
  identity = { ...identity, sessionRevision: 2 }
  assert.notEqual(module.exports.VirtualFundsPanel().key, first.key)
  identity = { ...identity, marketSwitchPending: true }
  assert.equal(module.exports.VirtualFundsPanel().type, "p")
  identity = { ...identity, marketSwitchPending: false }
  assert.equal(module.exports.VirtualFundsPanel().key, `${2}:fixture-user:virtual:futures:fixture-token`)
  identity = { ...identity, user: { ...identity.user, market_type: "stock" } }
  assert.equal(module.exports.VirtualFundsPanel().type, "p")
})

test("futures funds entry uses custom deposits with pending retries and no monthly claims", async () => {
  const panel = await readFile(new URL("./virtual-funds-panel.tsx", import.meta.url), "utf8")
  const assets = await readFile(new URL("../../app/(main)/assets/page.tsx", import.meta.url), "utf8")
  assert.match(panel, /depositPaperFunds/)
  assert.match(panel, /preparePaperDeposit/)
  assert.match(panel, /inputMode="decimal"/)
  assert.match(panel, /sessionStorage/)
  assert.doesNotMatch(panel + assets, /每月可|本月额度|领取本月|claimPaperFunds|本月已领取/)
  assert.match(panel, /模拟/)
})
