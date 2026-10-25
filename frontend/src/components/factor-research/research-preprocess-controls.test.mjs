import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"

const requireModule = createRequire(import.meta.url)
const defaults = { winsorize: "mad", standardize: true, fillna: true, industry_neutral: false, mktcap_neutral: false }
function mount(disabled = false) {
  const target = new URL("./research-preprocess-controls.tsx", import.meta.url)
  const source = existsSync(target) ? readFileSync(target, "utf8") : "export const ResearchPreprocessControls = undefined"
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const module = { exports: {} }
  vm.runInNewContext(output, { module, exports: module.exports, require: requireModule })
  assert.equal(typeof module.exports.ResearchPreprocessControls, "function", "editable preprocessing controls must exist")
  let value = { ...defaults }
  return { render: () => module.exports.ResearchPreprocessControls({ value, disabled, onChange: (next) => { value = next } }), value: () => value }
}
function nodes(tree) { if (Array.isArray(tree)) return tree.flatMap(nodes); return tree && typeof tree === "object" ? [tree, ...nodes(tree.props?.children)] : [] }
function control(page, label) { const field = nodes(page.render()).find((node) => node.props?.["aria-label"] === label); assert.ok(field, label); return field }

test("winsorization and all preprocessing switches change only their own schema fields", () => {
  const page = mount()
  control(page, "去极值方法").props.onChange({ target: { value: "3sigma" } })
  assert.equal(page.value().winsorize, "3sigma")
  for (const [label, key, value] of [["Z-Score标准化", "standardize", false], ["缺失值处理", "fillna", false], ["行业中性化", "industry_neutral", true], ["市值中性化", "mktcap_neutral", true]]) {
    control(page, label).props.onChange({ target: { checked: value } })
    assert.equal(page.value()[key], value)
    assert.equal(page.value().winsorize, "3sigma")
  }
  control(page, "去极值方法").props.onChange({ target: { value: "none" } })
  assert.equal(page.value().winsorize, "none")
  control(page, "去极值方法").props.onChange({ target: { value: "unsupported" } })
  assert.equal(page.value().winsorize, "none")
})

test("running job disables the parameter fieldset and ignores programmatic event callbacks", () => {
  const page = mount(true)
  assert.equal(page.render().props.disabled, true)
  control(page, "去极值方法").props.onChange({ target: { value: "none" } })
  control(page, "行业中性化").props.onChange({ target: { checked: true } })
  assert.deepEqual(page.value(), defaults)
})
