import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import vm from "node:vm"
import ts from "typescript"

const requireModule = createRequire(import.meta.url)
function nodes(tree) { return Array.isArray(tree) ? tree.flatMap(nodes) : tree && typeof tree === "object" ? [tree, ...nodes(tree.props?.children)] : [] }
function text(tree) { return Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }
const settle = () => new Promise((resolve) => setImmediate(resolve))

function mount(settingsPage) {
  const source = readFileSync(new URL(settingsPage ? "../../app/admin/settings/page.tsx" : "./user-quota-card.tsx", import.meta.url), "utf8")
  const slots = [], effects = [], calls = []
  let cursor = 0, first = true
  const react = {
    useState(initial) { const position = cursor++; if (!(position in slots)) slots[position] = typeof initial === "function" ? initial() : initial; return [slots[position], (value) => { slots[position] = typeof value === "function" ? value(slots[position]) : value }] },
    useEffect(callback) { if (first) effects.push(callback) },
  }
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const module = { exports: {} }
  const api = {
    getAdminSettingsApi: async () => ({ max_tasks_per_user: 5, factor_lab_daily_limit: 3, backtest_daily_limit: 2 }),
    getRegistrationStatusApi: async () => ({ registration_policy: "open" }),
    updateAdminSettingsApi: async (payload) => { calls.push(payload); return payload },
    getUserQuotaApi: async () => ({ max_tasks_override: 2, factor_lab_daily_override: 1, backtest_daily_override: 1, effective: { max_tasks: 2, factor_daily: null, backtest_daily: null }, used: { tasks: 1, factor_today: 999, backtest_today: 999 } }),
    updateAdminUserApi: async (_id, payload) => { calls.push(payload) },
  }
  vm.runInNewContext(output, { module, exports: module.exports, require: (name) => {
    if (name === "react") return react
    if (name === "@/lib/admin-api") return api
    if (name.startsWith("@/components/ui/")) return new Proxy({}, { get: (_target, key) => key })
    return requireModule(name)
  } })
  return { calls, render() { cursor = 0; const tree = settingsPage ? module.exports.default() : module.exports.UserQuotaCard({ userId: "owner" }); first = false; return tree }, async ready() { this.render(); effects.forEach((callback) => callback()); await settle() } }
}

for (const settingsPage of [false, true]) {
  test(`${settingsPage ? "global settings" : "user quota"} removes daily controls and never submits legacy daily limits`, async () => {
    const page = mount(settingsPage); await page.ready()
    const tree = page.render()
    assert.match(text(tree), /因子和回测使用次数不限/)
    assert.doesNotMatch(text(tree), /因子实验室\/天|回测\/天|因子\/天/)
    const save = nodes(tree).find((node) => node.type === "Button" && /保存/.test(text(node)))
    assert.ok(save)
    save.props.onClick(); await settle()
    assert.equal(page.calls.length, 1)
    for (const field of ["factor_lab_daily_limit", "backtest_daily_limit", "factor_lab_daily_override", "backtest_daily_override"]) assert.equal(field in page.calls[0], false)
    assert.ok("max_tasks_per_user" in page.calls[0] || "max_tasks_override" in page.calls[0])
  })
}
