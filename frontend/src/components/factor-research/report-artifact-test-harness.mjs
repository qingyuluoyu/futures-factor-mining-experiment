import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import vm from "node:vm"
import ts from "typescript"
import { jsx } from "react/jsx-runtime"

const requireModule = createRequire(import.meta.url)
export const firstId = "11111111-1111-4111-8111-111111111111"
export const secondId = "22222222-2222-4222-8222-222222222222"
export function elements(tree) { return Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : [] }
export function text(tree) { return Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }
export function button(tree, label) { const result = elements(tree).find((node) => node.type === "button" && text(node).includes(label)); assert.ok(result, label); return result }
export const settle = () => new Promise((resolve) => setImmediate(resolve))

export function mountArtifact(name) {
  const target = new URL(name === "actions" ? "./research-report-actions.tsx" : "./research-comparison-panel.tsx", import.meta.url)
  const source = existsSync(target) ? readFileSync(target, "utf8") : `export function ${name === "actions" ? "ResearchReportActions" : "ResearchComparisonPanel"}(){return null}`
  const slots = [], effects = [], calls = { exports: [], starts: [], compares: [] }
  let cursor = 0
  const controls = { auth: { user: { id: "owner", market_type: "stock" }, accessToken: "captured", sessionRevision: 1 }, download: null, comparison: null, error: null }
  const authStore = (select) => select ? select(controls.auth) : controls.auth
  authStore.getState = () => controls.auth
  const react = {
    useState: (initial) => { const slot = cursor++; if (!(slot in slots)) slots[slot] = typeof initial === "function" ? initial() : initial; return [slots[slot], (value) => { slots[slot] = typeof value === "function" ? value(slots[slot]) : value }] },
    useRef: (initial) => { const slot = cursor++; if (!(slot in slots)) slots[slot] = { current: initial }; return slots[slot] },
    useEffect: (callback, deps) => { const slot = cursor++; if (!(slot in slots) || deps.some((value, index) => value !== slots[slot].deps[index])) { slots[slot]?.cleanup?.(); slots[slot] = { deps }; effects.push(() => { slots[slot].cleanup = callback() }) } },
  }
  const module = { exports: {} }
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  vm.runInNewContext(output, { module, exports: module.exports, AbortController, Error, JSON, require: (path) => {
    if (path === "react") return react
    if (path === "@/stores/auth") return { useAuthStore: authStore }
    if (path === "@/lib/stock-research-api") return { stockResearchScope: (id, token) => id === "owner" && token === "captured" ? "owner:virtual" : "other:virtual" }
    if (path === "@/lib/stock-report-artifacts") return { downloadStockResearchReport: async (...args) => { calls.exports.push(args); if (controls.error) throw new Error(controls.error); return controls.download ?? { blob: {}, filename: "saved.json" } }, startResearchReportDownload: (value) => calls.starts.push(value), compareStockResearchReports: async (...args) => { calls.compares.push(args); if (controls.error) throw new Error(controls.error); return controls.comparison } }
    if (path.includes("mini-line-chart")) return { MiniLineChart: () => jsx("div", { children: "保存净值" }) }
    return requireModule(path)
  } })
  let props = name === "actions" ? { jobId: firstId, token: "captured", scope: "owner:virtual" } : { leftId: firstId, rightId: secondId, token: "captured", scope: "owner:virtual", onClear: () => {} }
  return { calls, controls, render: () => { cursor = 0; return module.exports[name === "actions" ? "ResearchReportActions" : "ResearchComparisonPanel"](props) }, change: (next) => { props = { ...props, ...next } }, async ready() { this.render(); effects.splice(0).forEach((effect) => effect()); await settle() } }
}
