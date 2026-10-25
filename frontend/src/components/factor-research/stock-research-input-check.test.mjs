import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import vm from "node:vm"
import ts from "typescript"

const elements = (tree) => Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : []
const text = (tree) => Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree)
const query = { start_date: "2026-09-01", end_date: "2026-09-03", factor_ids: ["pb"] }
const payload = { query, status: "missing_inputs", prices: { rows: 30, valid_close_rows: 30, invalid_rows: 0, duplicate_groups: 0, observed_dates: 3 }, fields: [{ field: "pb", unit: "ratio", source_rows: 30, usable_values: 0, missing_values: 0, nonfinite_values: 0, excluded_nonpositive: 30 }], factors: [{ factor_id: "pb", label: "市净率", status: "missing_inputs", reasons: ["no_usable_field_values"], minimum_observed_dates: 1 }], auxiliary_missing: [], limitations: ["候选值不是PIT认证"], read_at: "2026-10-20T00:00:00Z", inputs_version: "stock-research-inputs-v1" }

function mount() {
  const slots = [], effects = [], calls = []
  let cursor = 0, props = { token: "captured-token", scope: "owner:virtual", query }
  const dependencies = {
    react: {
      useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = initial; return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value }] },
      useRef(initial) { const index = cursor++; return slots[index] ??= { current: initial } },
      useEffect(callback, deps) { const index = cursor++; if (!(index in slots) || deps.some((value, position) => value !== slots[index].deps[position])) { slots[index]?.cleanup?.(); slots[index] = { deps }; effects.push(() => { slots[index].cleanup = callback() }) } },
    },
    "@/lib/stock-research-inputs-api": { getStockResearchInputs: (input, token, signal) => new Promise((resolve, reject) => calls.push({ input, token, signal, resolve, reject })) },
  }
  const source = readFileSync(new URL("./stock-research-input-check.tsx", import.meta.url), "utf8")
  const module = { exports: {} }, requireModule = createRequire(import.meta.url)
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText, { module, exports: module.exports, AbortController, Error, require: (name) => dependencies[name] ?? requireModule(name) })
  const render = () => { cursor = 0; const tree = module.exports.StockResearchInputCheck(props); effects.splice(0).forEach((effect) => effect()); return tree }
  const click = () => elements(render()).find((item) => item.type === "button").props.onClick()
  return { render, click, calls, change(patch) { props = { ...props, ...patch }; render() }, settle: () => new Promise(setImmediate), unmount() { slots.forEach((slot) => slot?.cleanup?.()) } }
}

test("input check is explicit and renders missing-domain reason without creating a task", async () => {
  const page = mount(); page.render(); assert.equal(page.calls.length, 0)
  page.click(); assert.equal(page.calls.length, 1); assert.equal(page.calls[0].token, "captured-token")
  page.calls[0].resolve(payload); await page.settle()
  const body = text(page.render())
  assert.match(body, /市净率.*金融域有效值/)
  assert.match(body, /非正排除/)
  assert.match(body, /不提交任务、不下载/)
})

test("query or login-mode changes cancel and hide old results including late replies", async () => {
  const page = mount(); page.render(); page.click()
  page.change({ scope: "owner:live", token: "next-token" })
  assert.equal(page.calls[0].signal.aborted, true)
  page.calls[0].resolve(payload); await page.settle(); assert.doesNotMatch(text(page.render()), /市净率/)
  page.click(); page.calls[1].resolve(payload); await page.settle(); assert.match(text(page.render()), /市净率/)
  page.change({ query: { ...query, factor_ids: ["roe"] } }); assert.doesNotMatch(text(page.render()), /市净率/)
  page.click(); page.unmount(); assert.equal(page.calls[2].signal.aborted, true)
})

test("error is not displayed as no inputs, retries are explicit, disabled checks stay idle", async () => {
  const page = mount(); page.render(); page.click()
  page.calls[0].reject(Object.assign(new Error("检查失败"), { request_id: "a".repeat(32) })); await page.settle()
  assert.match(text(page.render()), /检查失败.*请求/)
  assert.doesNotMatch(text(page.render()), /价格 0 行/)
  page.click(); assert.equal(page.calls.length, 2)
  page.change({ disabled: true }); page.click(); assert.equal(page.calls.length, 2)
})

test("stock workspace mounts the new explicit checker with current factors and scope", () => {
  const source = readFileSync(new URL("./factor-research-workspace.tsx", import.meta.url), "utf8")
  assert.match(source, /<StockResearchInputCheck token=\{token\} scope=\{scope\}/)
  assert.match(source, /factor_ids: requiredFactorIds/)
})
