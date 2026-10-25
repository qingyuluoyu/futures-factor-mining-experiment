import assert from "node:assert/strict"
import test from "node:test"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import vm from "node:vm"
import ts from "typescript"

function render(props) {
  const path = new URL("./research-coverage-panel.tsx", import.meta.url)
  assert.equal(existsSync(path), true)
  const output = ts.transpileModule(readFileSync(path, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const module = { exports: {} }
  vm.runInNewContext(output, { module, exports: module.exports, require: createRequire(import.meta.url) })
  return module.exports.ResearchCoveragePanel(props)
}
function elements(tree) { if (Array.isArray(tree)) return tree.flatMap(elements); return tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : [] }
function text(tree) { if (Array.isArray(tree)) return tree.map(text).join(""); return tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }

test("coverage distinguishes catalog from priced securities and makes limited guarantees explicit", () => {
  let applied = 0
  const tree = render({ coverage: { catalog_count: 5558, available: { securities: 300, first_trade_date: "2026-01-01", last_trade_date: "2026-09-30", observed_dates: 100 }, recommended: { start_date: "2026-07-01", end_date: "2026-09-30" }, requested: { start_date: "2026-07-01", end_date: "2026-09-30" }, selected: { securities: 300, observed_dates: 61, price_rows: 18000, missing_price_cells: 300, panel_cells: 164700 }, limits: { max_panel_cells: 2000000 }, status: "within_limits", warnings: ["本地来源未认证"] }, loading: false, error: "", onApply: () => applied++, onRefresh: () => {} })
  assert.match(text(tree), /目录.*5558/)
  assert.match(text(tree), /有价证券.*300/)
  assert.match(text(tree), /61.*观测日/)
  assert.match(text(tree), /缺价.*300/)
  assert.match(text(tree), /不保证.*计算成功/)
  assert.match(text(tree), /PIT|历史时点/)
  const button = elements(tree).find((element) => element.type === "button" && text(element).includes("使用最近覆盖区间"))
  button.props.onClick(); assert.equal(applied, 1)
})

test("failed, no-data and excessive coverage show actionable status and retry", () => {
  let retries = 0
  const failed = render({ coverage: null, loading: false, error: "覆盖连接失败", onRefresh: () => retries++, onApply: () => {} })
  assert.match(text(failed), /覆盖连接失败/)
  assert.ok(elements(failed).some((element) => element.props?.role === "alert"))
  elements(failed).find((element) => element.type === "button" && text(element).includes("重试")).props.onClick()
  assert.equal(retries, 1)
  for (const status of ["no_data", "over_limit"]) {
    const tree = render({ coverage: { catalog_count: 1, available: { securities: 0 }, recommended: null, requested: null, selected: { securities: 0, observed_dates: 0, price_rows: 0, missing_price_cells: 0, panel_cells: 0 }, limits: { max_panel_cells: 2 }, status, warnings: [] }, loading: false, error: "", onRefresh: () => {}, onApply: () => {} })
    assert.match(text(tree), status === "no_data" ? /无.*数据/ : /超.*限/)
  }
})
