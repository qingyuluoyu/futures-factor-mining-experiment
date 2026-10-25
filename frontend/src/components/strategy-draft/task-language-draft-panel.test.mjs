import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import vm from "node:vm"
import test from "node:test"
import ts from "typescript"
import * as api from "../../lib/task-language-draft.ts"

const modelId = "11111111-1111-4111-8111-111111111111"
const context = { contracts: [{ symbol: "rb2701", name: "螺纹钢2701", exchange: "SHFE" }], models: [{ id: modelId, display_name: "用户模型", model_id: "chat", provider_api_type: "openai", capabilities: [] }] }
const description = "rb2701，15分钟，双均线，快线5，慢线20，只做多，2手，资金十万元"
const path = new URL("./task-language-draft-panel.tsx", import.meta.url)
function mount() {
  assert.equal(existsSync(path), true)
  const slots = [], pending = []
  const auth = { accessToken: "token", user: { id: "user" } }
  const controls = { reply: context, failure: false, active: true }
  const calls = { loads: [], applies: [] }
  let cursor = 0
  const react = {
    useState(initial) { const position = cursor++; if (!(position in slots)) slots[position] = typeof initial === "function" ? initial() : initial; return [slots[position], (value) => { slots[position] = typeof value === "function" ? value(slots[position]) : value }] },
    useRef(initial) { const position = cursor++; if (!(position in slots)) slots[position] = { current: initial }; return slots[position] },
    useEffect(effect, deps) { const position = cursor++; if (!(position in slots) || deps.some((value, index) => value !== slots[position].deps[index])) { slots[position]?.cleanup?.(); slots[position] = { deps }; pending.push(() => { slots[position].cleanup = effect() }) } },
  }
  const useAuthStore = (selector) => selector(auth)
  useAuthStore.getState = () => auth
  const dependencies = { react, "@/stores/auth": { useAuthStore }, "@/lib/task-language-draft": { ...api, loadTaskLanguageContext: async (token, models, signal) => { calls.loads.push({ token, models, signal }); if (controls.failure) throw new Error("目录连接失败"); return controls.reply } } }
  const output = ts.transpileModule(readFileSync(path, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const module = { exports: {} }, requireModule = createRequire(import.meta.url)
  vm.runInNewContext(output, { module, exports: module.exports, require: (name) => dependencies[name] ?? requireModule(name), AbortController, Error })
  const render = () => { cursor = 0; const tree = module.exports.TaskLanguageDraftPanel({ target: "quant_task", active: controls.active, onApply: (draft) => calls.applies.push(draft) }); pending.splice(0).forEach((effect) => effect()); return tree }
  render()
  return { render, calls, controls, auth, edit(value) { elements(render()).find((element) => element.type === "textarea").props.onChange({ target: { value } }) }, unmount() { slots.forEach((slot) => slot?.cleanup?.()) } }
}
function elements(tree) { if (Array.isArray(tree)) return tree.flatMap(elements); return tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : [] }
function text(tree) { if (Array.isArray(tree)) return tree.map(text).join(""); return tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }
const button = (page, label) => { const found = elements(page.render()).find((element) => element.type === "button" && text(element).includes(label)); assert.ok(found, label); return found }
const settle = () => new Promise((resolve) => setImmediate(resolve))

test("rule parsing previews a schema-validated draft and requires explicit form application", async () => {
  const page = mount()
  assert.match(text(page.render()), /规则解析/)
  assert.match(text(page.render()), /不调用.*模型/)
  page.edit(description)
  button(page, "解析配置草稿").props.onClick(); await settle()
  assert.equal(page.calls.applies.length, 0)
  assert.match(text(page.render()), /100000/)
  assert.match(text(page.render()), /2.*手/)
  button(page, "确认填入表单").props.onClick()
  assert.equal(page.calls.applies.length, 1)
  assert.equal(page.calls.applies[0].auto_start, false)
  assert.equal(button(page, "确认填入表单").props.disabled, true)
  assert.equal(page.calls.loads[0].token, "token")
  page.edit(`${description}，上涨3%才开仓`)
  assert.equal(elements(page.render()).some((element) => text(element).includes("确认填入表单") && element.type === "button"), false)
  button(page, "解析配置草稿").props.onClick(); await settle()
  assert.match(text(page.render()), /待补充|未支持/)
  assert.equal(page.calls.applies.length, 1)
})

test("old responses after edits, session switch, dialog close or unmount cannot expose a draft", async () => {
  for (const boundary of ["edit", "session", "close", "unmount"]) {
    const page = mount()
    let finish
    page.controls.reply = new Promise((resolve) => { finish = resolve })
    page.edit(description); button(page, "解析配置草稿").props.onClick()
    if (boundary === "edit") page.edit("新的尚未明确描述")
    else if (boundary === "session") { page.auth.accessToken = "new-token"; assert.equal(elements(page.render()).find((element) => element.type === "textarea").props.value, "") }
    else if (boundary === "close") { page.controls.active = false; page.render() }
    else page.unmount()
    finish(context); await settle()
    assert.equal(page.calls.applies.length, 0)
    assert.equal(elements(page.render()).some((element) => element.type === "button" && text(element).includes("确认填入表单")), false)
    assert.equal(page.calls.loads[0].signal.aborted, true)
  }
})

test("catalog failure stays visible and only manual retry performs another bounded read", async () => {
  const page = mount(); page.controls.failure = true; page.edit(description)
  button(page, "解析配置草稿").props.onClick(); await settle()
  assert.match(text(page.render()), /目录连接失败/)
  assert.equal(page.calls.loads.length, 1)
  page.controls.failure = false
  button(page, "解析配置草稿").props.onClick(); await settle()
  assert.equal(page.calls.loads.length, 2)
  assert.ok(button(page, "确认填入表单"))
})
