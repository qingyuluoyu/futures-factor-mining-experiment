import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"
import { createRequire } from "node:module"

test("neither market workspace mounts before server market confirmation", () => {
  const source = readFileSync(new URL("./stock-feature-boundary.tsx", import.meta.url), "utf8")
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const requireModule = createRequire(import.meta.url), module = { exports: {} }
  let identity = { marketSwitchPending: true, user: { market_type: "futures" } }
  vm.runInNewContext(output, { module, exports: module.exports, require: (name) => {
    if (name === "@/stores/auth") return { useAuthStore: (selector) => selector(identity) }
    if (name === "@/lib/market-routes") return { supportsStockRoute: () => true }
    if (name === "next/navigation") return { usePathname: () => "/trading" }
    return requireModule(name)
  } })
  for (const market of ["futures", "stock"]) {
    identity = { marketSwitchPending: true, user: { market_type: market } }
    const blocked = module.exports.StockFeatureBoundary({ children: "private-workspace" })
    assert.notEqual(blocked.props.children, "private-workspace")
    assert.equal(blocked.props.role, "status")
    identity = { ...identity, marketSwitchPending: false }
    assert.equal(module.exports.StockFeatureBoundary({ children: "private-workspace" }).props.children, "private-workspace")
  }
})
