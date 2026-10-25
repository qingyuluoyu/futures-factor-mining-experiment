import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createHash } from "node:crypto"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"
import * as researchApi from "../../lib/stock-research-api.ts"

const requireModule = createRequire(import.meta.url)
const Workspace = () => null
const ModelLibrary = () => null
const FactorLibrary = () => null
const output = ts.transpileModule(readFileSync(new URL("./stock-research-entry.tsx", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
function elements(tree) { return Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : [] }
function mount(auth, props = {}) {
  const module = { exports: {} }
  const slots = []; let cursor = 0
  const dependencies = {
    react: {
      useRef(value) { const i = cursor++; if (!(i in slots)) slots[i] = { current: value }; return slots[i] },
      useState(value) { const i = cursor++; if (!(i in slots)) slots[i] = value; return [slots[i], (next) => { slots[i] = typeof next === "function" ? next(slots[i]) : next }] },
    },
    "next/link": { default: "a" },
    "@/components/factor-research/factor-research-workspace": { FactorResearchWorkspace: Workspace },
    "@/components/factor-research/stock-model-library": { StockModelLibrary: ModelLibrary },
    "@/components/factor-research/stock-factor-library": { StockFactorLibrary: FactorLibrary },
    "@/lib/stock-research-api": researchApi,
    "@/stores/auth": { useAuthStore: (selector) => selector(auth) },
  }
  vm.runInNewContext(output, { module, exports: module.exports, require: (name) => dependencies[name] ?? requireModule(name) })
  return { render: () => { cursor = 0; return module.exports.StockResearchEntry(props) }, setAuth: (value) => { auth = value } }
}
function render(auth, props = {}) { return mount(auth, props).render() }
function workspace(tree) { return elements(tree).find((node) => node.type === Workspace) }
const token = (sub, mode) => `header.${Buffer.from(JSON.stringify({ sub, trading_mode: mode })).toString("base64url")}.test-only-unsigned-payload`

test("research entry only mounts after user and token mode are restored consistently", () => {
  const auth = { loaded: true, user: { id: "user", market_type: "stock" }, accessToken: token("user", "virtual") }
  const tree = workspace(render(auth, { initialTab: "backtest", title: "A股历史回测" }))
  assert.equal(tree.type, Workspace)
  assert.equal(tree.props.initialTab, "backtest")
  assert.equal(tree.props.scope, "user:virtual")
  assert.equal(tree.props.title, "A股历史回测")
  assert.notEqual(tree.key, workspace(render({ ...auth, accessToken: token("user", "live") }, { initialTab: "backtest" })).key)
  assert.notEqual(tree.key, workspace(render(auth, { initialTab: "analysis" })).key)
})

test("hydration and expired identity never mount the financial workspace", () => {
  assert.notEqual(render({ loaded: false, user: null, accessToken: null }).type, Workspace)
  for (const auth of [{ loaded: true, user: null, accessToken: null }, { loaded: true, user: { id: "user" }, accessToken: token("other", "virtual") }]) {
    const tree = render(auth)
    assert.notEqual(tree.type, Workspace)
    assert.ok(elements(tree).some((item) => item.props?.href === "/login"))
  }
})

test("same-token login revision remounts research and leaving stock unmounts workspace", () => {
  const auth = { sessionRevision: 1, loaded: true, user: { id: "user", market_type: "stock" }, accessToken: token("user", "virtual") }
  const original = workspace(render(auth))
  assert.notEqual(original.key, workspace(render({ ...auth, sessionRevision: 2 })).key)
  assert.notEqual(render({ ...auth, user: { ...auth.user, market_type: "futures" } }).type, Workspace)
})

test("model library mounts only after explicit stock user action and cannot carry an open panel across sessions", () => {
  const auth = { sessionRevision: 1, loaded: true, user: { id: "user", market_type: "stock" }, accessToken: token("user", "virtual") }
  const page = mount(auth)
  const before = workspace(page.render())
  assert.ok(!elements(page.render()).some((node) => node.type === ModelLibrary))
  const open = elements(page.render()).find((node) => node.type === "button" && node.props?.children === "查看本人模型历史")
  assert.ok(open, "explicit stock-only model history action exists")
  open.props.onClick()
  assert.equal(elements(page.render()).filter((node) => node.type === ModelLibrary).length, 1)
  assert.equal(workspace(page.render()).key, before.key)
  page.setAuth({ ...auth, sessionRevision: 2 })
  assert.ok(!elements(page.render()).some((node) => node.type === ModelLibrary))
  page.setAuth(auth)
  assert.ok(!elements(page.render()).some((node) => node.type === ModelLibrary), "returning to an earlier identity does not reopen")
  for (const value of [{ ...auth, loaded: false }, { ...auth, user: { ...auth.user, market_type: "futures" } }, { ...auth, accessToken: token("other", "virtual") }]) {
    page.setAuth(value)
    assert.ok(!elements(page.render()).some((node) => node.type === ModelLibrary || node.type === Workspace || node.type === "button"))
  }
})

test("entry routes library, analysis and backtest to different business content while retaining identity gates", () => {
  const auth = { sessionRevision: 1, loaded: true, user: { id: "user", market_type: "stock" }, accessToken: token("user", "virtual") }
  assert.equal(workspace(render(auth)).props.view, "analysis")
  for (const view of ["analysis", "backtest"]) {
    const tree = render(auth, { view })
    assert.equal(workspace(tree).props.view, view)
    assert.equal(elements(tree).some((node) => node.type === FactorLibrary), false)
  }
  const tree = render(auth, { view: "library" })
  assert.equal(workspace(tree), undefined)
  const library = elements(tree).find((node) => node.type === FactorLibrary)
  assert.equal(library.props.scope, "user:virtual")
  assert.equal(library.props.token, auth.accessToken)
  assert.equal(elements(tree).some((node) => node.type === ModelLibrary || node.type === "button"), false)
  assert.equal(elements(render({ ...auth, loaded: false }, { view: "library" })).some((node) => node.type === FactorLibrary), false)
  assert.notEqual(library.key, elements(render({ ...auth, sessionRevision: 2 }, { view: "library" })).find((node) => node.type === FactorLibrary).key)
})

test("stock route changes only add the business view and leave the complete futures source unchanged", () => {
  for (const [name, view, beforeSha] of [["factor-lab", "library", "517f5f9cb2249959abb0599b7d9ec55ef00bd8cdf7460855eeec1725cdc95f92"], ["backtest", "backtest", "c9f6f201dc408fef5de737ad7bf26923d6affa2d380a3c4ea7cc01f893b9bbd2"]]) {
    const route = readFileSync(new URL(`../../app/(main)/${name}/page.tsx`, import.meta.url), "utf8")
    assert.ok(route.includes(`view="${view}"`))
    assert.equal(createHash("sha256").update(route.replace(` view="${view}"`, "")).digest("hex"), beforeSha)
  }
})
