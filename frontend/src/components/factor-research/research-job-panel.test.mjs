import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import vm from "node:vm"
import { createRequire } from "node:module"
import ts from "typescript"
import { isActiveResearchJob } from "../../lib/stock-research-api.ts"

const source = readFileSync(new URL("./research-job-panel.tsx", import.meta.url), "utf8")
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
const module = { exports: {} }, requireModule = createRequire(import.meta.url)
vm.runInNewContext(output, { module, exports: module.exports, require: (name) => name === "next/link" ? { default: () => null } : name === "@/lib/stock-research-api" ? { isActiveResearchJob } : requireModule(name) })
function elements(tree) { if (Array.isArray(tree)) return tree.flatMap(elements); return tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : [] }
function text(tree) { if (Array.isArray(tree)) return tree.map(text).join(""); return tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }

test("unavailable worker exposes a manual availability refresh without resubmitting work", async () => {
  let refreshes = 0, retries = 0
  const tree = module.exports.ResearchJobPanel({ task: { availability: { enabled: true, accepting: false }, refreshAvailability: async () => { refreshes++ }, retry: async () => { retries++ } } })
  const button = elements(tree).find((element) => element.type === "button" && text(element).includes("刷新服务状态"))
  assert.ok(button)
  button.props.onClick(); await Promise.resolve()
  assert.equal(refreshes, 1)
  assert.equal(retries, 0)
})

test("queued cancellation is visible and its button cannot send repeated requests", () => {
  const tree = module.exports.ResearchJobPanel({ task: { availability: { enabled: true, accepting: true }, job: { status: "running" }, cancelling: true } })
  const button = elements(tree).find((element) => element.type === "button" && text(element).includes("取消"))
  assert.ok(button)
  assert.equal(button.props.disabled, true)
  assert.match(text(tree), /取消处理中/)
})

test("unconfirmed recovery explains why new jobs and restore actions are blocked", () => {
  const tree = module.exports.ResearchJobPanel({ task: { availability: { enabled: true, accepting: true }, recoveryPending: true } })
  assert.match(text(tree), /正在核对任务恢复记录/)
})
