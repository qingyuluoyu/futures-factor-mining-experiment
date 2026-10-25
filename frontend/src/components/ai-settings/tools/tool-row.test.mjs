import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"
import { renderToStaticMarkup } from "react-dom/server"

const requireModule = createRequire(import.meta.url)
const source = readFileSync(new URL("./tool-row.tsx", import.meta.url), "utf8")
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
const module = { exports: {} }
vm.runInNewContext(output, { module, exports: module.exports, require: (name) => name === "@/lib/utils" ? { cn: (...values) => values.filter(Boolean).join(" ") } : requireModule(name) })

function nodes(tree) { return Array.isArray(tree) ? tree.flatMap(nodes) : tree && typeof tree === "object" ? [tree, ...nodes(tree.props?.children)] : [] }

test("blocked chat actions show the real reason and cannot enable even with stale free-trade flags", () => {
  const calls = []
  const tool = { name: "paper_open_long", name_zh: "模拟开仓", category: "模拟交易", description_zh: "测试", requires_confirmation: true, supports_free_trade: true, free_trade: true, enabled: true, availability: "server_confirmation_required", unavailable_reason: "服务器确认尚未开放" }
  const tree = module.exports.ToolRow({ tool, toggling: null, onToggleEnabled: () => calls.push("enable"), onToggleFreeTrade: () => calls.push("free") })
  const markup = renderToStaticMarkup(tree)
  assert.ok(markup.includes("暂未开放"))
  assert.ok(markup.includes(tool.unavailable_reason))
  assert.ok(!markup.includes("已启用"))
  const switches = nodes(tree).filter((node) => node.type === module.exports.ToggleSwitch)
  assert.equal(switches.length, 1)
  assert.equal(switches[0].props.disabled, true)
  switches[0].props.onClick()
  assert.deepEqual(calls, [])
})

test("available read-only tools retain their enabled toggle", () => {
  let called = false
  const tree = module.exports.ToolRow({ tool: { name: "paper_list_positions", requires_confirmation: false, enabled: true, supports_free_trade: false, availability: "available" }, toggling: null, onToggleEnabled: () => { called = true }, onToggleFreeTrade: () => {} })
  const control = nodes(tree).find((node) => node.type === module.exports.ToggleSwitch)
  assert.equal(control.props.disabled, false)
  control.props.onClick()
  assert.equal(called, true)
})
