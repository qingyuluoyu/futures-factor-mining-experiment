import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"
import * as marketRoutes from "../../lib/market-routes.ts"

const requireModule = createRequire(import.meta.url)
function elements(tree) { return Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : [] }
function text(tree) { return Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }
function renderFile(file, name, stock, pathname = "/dashboard") {
  const source = readFileSync(new URL(file, import.meta.url), "utf8")
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const dependencies = {
    react: { useState: (initial) => [initial, () => {}], useEffect: () => {} },
    "next/navigation": { usePathname: () => pathname },
    "next/link": { default: "a" },
    "@/lib/market-routes": marketRoutes,
    "@/lib/utils": { cn: (...values) => values.filter(Boolean).join(" ") },
    "@/stores/auth": { useAuthStore: (selector) => selector({ user: { market_type: stock ? "stock" : "futures" } }) },
    "@/stores/app": { useAppStore: () => ({ sidebarCollapsed: false, toggleSidebar: () => {} }) },
  }
  const module = { exports: {} }
  vm.runInNewContext(output, { module, exports: module.exports, require: (target) => dependencies[target] ?? requireModule(target) })
  return module.exports[name]({ children: "CONNECTED_CHILD" })
}

test("stock sidebar distinguishes unfinished modules without marking connected pages unavailable", () => {
  const stock = renderFile("./sidebar.tsx", "Sidebar", true)
  for (const path of ["/ai-market", "/ai-trading", "/strategy-favorites", "/messages"]) {
    const link = elements(stock).find((item) => item.props?.href === path)
    assert.ok(link); assert.match(text(link), /未接通/)
  }
  for (const path of ["/backtest", "/factor-lab", "/history"]) {
    assert.doesNotMatch(text(elements(stock).find((item) => item.props?.href === path)), /未接通/)
  }
  const futures = renderFile("./sidebar.tsx", "Sidebar", false)
  assert.doesNotMatch(text(futures), /未接通/)
})

test("unfinished stock boundary no longer says paper replay is unimplemented", () => {
  const tree = renderFile("./stock-feature-boundary.tsx", "StockFeatureBoundary", true, "/ai-trading")
  assert.doesNotMatch(text(tree), /股票模拟交易接通前/)
  for (const path of ["/market", "/trading", "/factor-lab", "/backtest"]) assert.ok(elements(tree).some((item) => item.props?.href === path))
  assert.equal(text(renderFile("./stock-feature-boundary.tsx", "StockFeatureBoundary", true, "/backtest")), "CONNECTED_CHILD")
})
