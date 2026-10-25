import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"
import { jsx } from "react/jsx-runtime"
import { renderToStaticMarkup } from "react-dom/server"

const requireModule = createRequire(import.meta.url)
const user = { id: "11111111-1111-4111-8111-111111111111", username: "actual_qa_user", email: "original@example.test", phone: null, role: "user", real_name_verified: false, created_at: "2026-10-18T01:30:00Z", market_type: "stock", trading_mode: "virtual" }
const settle = () => new Promise((resolve) => setImmediate(resolve))

function mount() {
  const target = new URL("./personal-info-panel.tsx", import.meta.url)
  const source = existsSync(target) ? readFileSync(target, "utf8") : readFileSync(new URL("../../app/(main)/profile/page.tsx", import.meta.url), "utf8").replace("function PersonalInfoPanel()", "export function PersonalInfoPanel()")
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const slots = [], effects = [], cleanups = [], calls = []
  let cursor = 0, initial = true
  const controls = { save: null, fail: false, auth: { user: { ...user }, accessToken: "synthetic-token", sessionRevision: 1, updateUser: (patch) => { controls.auth.user = { ...controls.auth.user, ...patch } } } }
  const authStore = (selector) => selector ? selector(controls.auth) : controls.auth
  authStore.getState = () => controls.auth
  const react = { useState: (value) => { const slot = cursor++; if (!(slot in slots)) slots[slot] = typeof value === "function" ? value() : value; return [slots[slot], (next) => { slots[slot] = typeof next === "function" ? next(slots[slot]) : next }] }, useRef: (value) => { const slot = cursor++; if (!(slot in slots)) slots[slot] = { current: value }; return slots[slot] }, useEffect: (effect, deps) => { const slot = cursor++; if (initial || deps?.some((value, index) => value !== slots[slot]?.[index])) { slots[slot] = deps; effects.push(effect) } }, useCallback: (callback) => callback }
  const ui = new Proxy({}, { get: (_, name) => (props) => jsx(name === "Input" ? "input" : name === "Button" ? "button" : "div", props) })
  const module = { exports: {} }
  vm.runInNewContext(output, { module, exports: module.exports, Error, Date, require: (name) => {
    if (name === "react") return react
    if (name === "@/stores/auth") return { useAuthStore: authStore }
    if (name === "@/lib/api") return { getProfileApi: async () => ({ ...user }), updateProfileApi: async (...args) => { calls.push(args); if (controls.save) return controls.save; if (controls.fail) throw new Error("邮箱已被其他用户使用"); return { ...user, ...args[1], market_type: "futures", trading_mode: "live" } } }
    if (name === "@/lib/utils") return { cn: (...values) => values.join(" ") }
    if (name.startsWith("@/components/") || name === "lucide-react") return ui
    if (name === "@/lib/market-routes") return { supportsStockProfileTab: () => true }
    return requireModule(name)
  } })
  function render() { cursor = 0; const tree = module.exports.PersonalInfoPanel(); initial = false; return tree }
  render()
  return { render, controls, calls, async ready() { effects.splice(0).forEach((effect) => cleanups.push(effect())); await settle() }, unmount() { cleanups.forEach((cleanup) => cleanup?.()) } }
}

function nodes(tree) { return !tree || typeof tree !== "object" ? [] : [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)] }
function button(tree, label) { return nodes(tree).find((node) => (typeof node.props?.onClick === "function") && [node.props.children].flat().includes(label)) }
function input(tree, label) { return nodes(tree).find((node) => node.props?.["aria-label"] === label) }
async function save(page) { const tree = page.render(); const form = nodes(tree).find((node) => node.type === "form"); if (form) await form.props.onSubmit({ preventDefault() {} }); else button(tree, "保存").props.onClick() }

test("personal info displays actual identity and never invents verification or timestamps", async () => {
  const page = mount(); await page.ready()
  const html = renderToStaticMarkup(page.render())
  for (const value of [user.username, user.id, user.email, "未认证"]) assert.ok(html.includes(value), value)
  for (const value of ["quant_trader_01", "USR20250101", "138****8888", "2025-01-15"]) assert.ok(!html.includes(value), value)
})

test("saving updates real fields but retains the current market and login mode", async () => {
  const page = mount(); await page.ready(); button(page.render(), "编辑").props.onClick()
  const field = input(page.render(), "邮箱"); assert.ok(field, "email input must be editable")
  field.props.onChange({ target: { value: "updated@example.test" } }); await save(page)
  assert.equal(page.calls.length, 1)
  assert.equal(page.calls[0][0], "synthetic-token")
  assert.deepEqual(JSON.parse(JSON.stringify(page.calls[0][1])), { email: "updated@example.test" })
  assert.equal(page.controls.auth.user.email, "updated@example.test")
  assert.equal(page.controls.auth.user.market_type, "stock")
  assert.equal(page.controls.auth.user.trading_mode, "virtual")
})

test("cancel discards unsaved changes without sending a write", async () => {
  const page = mount(); await page.ready(); button(page.render(), "编辑").props.onClick()
  const field = input(page.render(), "邮箱"); assert.ok(field)
  field.props.onChange({ target: { value: "discard@example.test" } })
  button(page.render(), "取消").props.onClick()
  assert.equal(page.calls.length, 0)
  assert.ok(renderToStaticMarkup(page.render()).includes(user.email))
  assert.ok(!renderToStaticMarkup(page.render()).includes("discard@example.test"))
})

test("conflict is visible and preserves the draft for correction", async () => {
  const page = mount(); await page.ready(); page.controls.fail = true; button(page.render(), "编辑").props.onClick()
  const field = input(page.render(), "邮箱"); assert.ok(field)
  field.props.onChange({ target: { value: "conflict@example.test" } }); await save(page)
  assert.ok(renderToStaticMarkup(page.render()).includes("邮箱已被其他用户使用"))
  assert.equal(input(page.render(), "邮箱").props.value, "conflict@example.test")
  assert.equal(page.controls.auth.user.email, user.email)
})

test("late profile response cannot update a new session or double-submit", async () => {
  const page = mount(); await page.ready(); button(page.render(), "编辑").props.onClick()
  const field = input(page.render(), "邮箱"); assert.ok(field)
  field.props.onChange({ target: { value: "late@example.test" } })
  let resolveSave; page.controls.save = new Promise((resolve) => { resolveSave = resolve })
  const first = save(page), second = save(page)
  assert.equal(page.calls.length, 1)
  page.controls.auth = { ...page.controls.auth, sessionRevision: 2, user: { ...user, id: "new-user" } }
  resolveSave({ ...user, email: "late@example.test" }); await Promise.all([first, second])
  assert.equal(page.controls.auth.user.id, "new-user")
  assert.equal(page.controls.auth.user.email, user.email)
})

test("token refresh in the same session does not discard an unsaved profile draft", async () => {
  const page = mount(); await page.ready(); button(page.render(), "编辑").props.onClick()
  input(page.render(), "邮箱").props.onChange({ target: { value: "unsaved@example.test" } })
  page.controls.auth.accessToken = "refreshed-same-session"
  page.render(); await page.ready()
  assert.equal(input(page.render(), "邮箱").props.value, "unsaved@example.test")
})
