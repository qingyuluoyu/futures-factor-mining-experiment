import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"

const requireModule = createRequire(import.meta.url)
const Editor = () => null
const rows = [{ id: "ret_20d", label: "20日收益率", group: "动量", direction: "high", formula: "close / lag(close, 20) - 1", source: "daily", description: "20个观测日价格变化", method_version: "method-v1", missing_value_policy: "缺失明确排除" }]
function elements(tree) { return Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : [] }
function text(tree) { return Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }
const settle = () => new Promise((resolve) => setImmediate(resolve))
function mount({ fail = false, data = rows, deferred } = {}) {
  const output = ts.transpileModule(readFileSync(new URL("./stock-factor-library.tsx", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const slots = []; const effects = []; let cursor = 0
  const controls = { fail, data, deferred }; const calls = { catalog: 0 }
  const dependencies = {
    react: {
      useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === "function" ? initial() : initial; return [slots[i], (value) => { slots[i] = typeof value === "function" ? value(slots[i]) : value }] },
      useRef(initial) { const i = cursor++; if (!(i in slots)) slots[i] = { current: initial }; return slots[i] },
      useEffect(callback, deps) { const i = cursor++; if (!(i in slots) || deps.some((value, j) => value !== slots[i].deps[j])) { slots[i]?.cleanup?.(); slots[i] = { deps }; effects.push(() => { slots[i].cleanup = callback() }) } },
    },
    "next/link": { default: "a" },
    "@/components/factor-research/stock-factor-editor": { StockFactorEditor: Editor },
    "@/lib/stock-api": { getStockFactorsApi: async () => { calls.catalog++; if (controls.fail) throw new Error("目录读取失败"); return controls.deferred ?? controls.data } },
  }
  const module = { exports: {} }
  vm.runInNewContext(output, { module, exports: module.exports, require: (name) => { if (dependencies[name]) return dependencies[name]; if (name.startsWith("@/")) throw new Error(`Unexpected financial execution dependency: ${name}`); return requireModule(name) } })
  const render = () => { cursor = 0; const tree = module.exports.StockFactorLibrary({ token: "token", scope: "user:virtual" }); effects.splice(0).forEach((effect) => effect()); return tree }
  render()
  return { render, controls, calls, ready: async () => { await settle(); render(); await settle() }, unmount() { slots.forEach((slot) => slot?.cleanup?.()) } }
}

test("factor library presents actual formulas and provenance with definition editing and no research submission", async () => {
  const page = mount(); await page.ready()
  assert.match(text(page.render()), /A股因子实验室/)
  for (const value of [rows[0].label, rows[0].formula, rows[0].source, rows[0].description, rows[0].method_version, rows[0].missing_value_policy]) assert.ok(text(page.render()).includes(value))
  assert.doesNotMatch(text(page.render()), /开始检验|开始回测|手续费|训练模型/)
  assert.equal(elements(page.render()).some((node) => node.type === "input" && node.props.type === "date"), false)
  const editor = elements(page.render()).find((node) => node.type === Editor)
  assert.equal(editor.props.token, "token"); assert.equal(editor.props.scope, "user:virtual")
  for (const href of ["/factor-research", "/backtest"]) assert.ok(elements(page.render()).some((node) => node.props?.href === href))
  assert.equal(page.calls.catalog, 1)
  page.unmount()
})

test("factor library distinguishes catalog failure and empty data, and retry only rereads the catalog", async () => {
  const page = mount({ fail: true }); await page.ready()
  assert.match(text(page.render()), /目录读取失败/)
  page.controls.fail = false
  elements(page.render()).find((node) => node.type === "button" && text(node).includes("重试因子目录")).props.onClick()
  page.render(); await page.ready()
  assert.match(text(page.render()), /20日收益率/)
  assert.equal(page.calls.catalog, 2)
  page.unmount()
  const empty = mount({ data: [] }); await empty.ready()
  assert.match(text(empty.render()), /暂无内置因子/)
  assert.doesNotMatch(text(empty.render()), /目录读取失败/)
  empty.unmount()
})

test("unmounted factor library ignores a late catalog response", async () => {
  let resolve; const deferred = new Promise((done) => { resolve = done })
  const page = mount({ deferred }); page.unmount(); resolve(rows); await settle()
  assert.doesNotMatch(text(page.render()), /20日收益率/)
})
