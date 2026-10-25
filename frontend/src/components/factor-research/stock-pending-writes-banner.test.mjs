import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"
import { loadPendingWrites, memoryStorage } from "../../lib/stock-pending-writes-harness.mjs"

const requireModule = createRequire(import.meta.url)
// JSX 不调用组件函数，只产出 type 为该函数的元素；按标识比对才能认出链接。
const Link = ({ href }) => ({ type: "a", props: { href } })
const key = "74490e6b-b979-4331-87d8-c25b809075dd"
const settle = () => new Promise((resolve) => setImmediate(resolve))

function entry(kind, overrides = {}) {
  return { kind, idempotency_key: overrides.idempotency_key ?? key, signature: overrides.signature ?? "[]", created_at: "2026-10-26T00:00:00.000Z" }
}

function mount({ storage, scope = "owner:virtual", ignore } = {}) {
  const store = storage ?? memoryStorage()
  const slots = [], effects = []
  let cursor = 0
  const props = { scope, ignore }
  const dependencies = {
    react: {
      useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = initial; return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value }] },
      useRef(initial) { const index = cursor++; if (!(index in slots)) slots[index] = { current: initial }; return slots[index] },
      useEffect(callback, values) { const index = cursor++; if (!(index in slots) || values.some((value, position) => value !== slots[index].values[position])) { slots[index]?.cleanup?.(); slots[index] = { values }; effects.push(() => { slots[index].cleanup = callback() }) } },
    },
    "next/link": { default: Link },
    "@/lib/stock-pending-writes": loadPendingWrites(store, { crypto: globalThis.crypto }),
  }
  const target = new URL("./stock-pending-writes-banner.tsx", import.meta.url)
  const source = readFileSync(target, "utf8")
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const module = { exports: {} }
  vm.runInNewContext(output, { module, exports: module.exports, AbortController, Error, require: (name) => dependencies[name] ?? requireModule(name) })
  assert.equal(typeof module.exports.StockPendingWritesBanner, "function")
  const render = () => { cursor = 0; const tree = module.exports.StockPendingWritesBanner(props); effects.splice(0).forEach((effect) => effect()); return tree }
  const ready = async () => { for (let index = 0; index < 3; index++) { render(); await settle() } }
  render()
  return { render, ready, store, unmount: () => slots.forEach((slot) => slot?.cleanup?.()) }
}
function nodes(tree) { if (Array.isArray(tree)) return tree.flatMap(nodes); return tree && typeof tree === "object" ? [tree, ...nodes(tree.props?.children)] : [] }
function text(tree) { if (Array.isArray(tree)) return tree.map(text).join(""); return tree && typeof tree === "object" ? text(tree.props?.children) : String(tree ?? "") }
function links(tree) { return nodes(tree).filter((node) => node.type === Link).map((node) => node.props.href) }
const scopeKey = "stock-pending-writes:owner:virtual"

test("an empty registry renders nothing at all", async () => {
  const view = mount()
  await view.ready()
  assert.equal(view.render(), null)
  view.unmount()
})

test("unconfirmed writes are announced with a route to the component that can retry them", async () => {
  const store = memoryStorage({ [scopeKey]: JSON.stringify([
    entry("replay-order", { idempotency_key: "k1" }), entry("replay-step", { idempotency_key: "k2" }),
    entry("dataset-job", { idempotency_key: "k3" }), entry("custom-factor-save", { idempotency_key: "k4" }),
  ]) })
  const view = mount({ storage: store })
  await view.ready()
  const rendered = text(view.render())
  assert.match(rendered, /4\s*次/)
  assert.ok(links(view.render()).includes("/trading"))
  assert.ok(links(view.render()).includes("/factor-research"))
  // 绝不把存储内容原样倒出来，也不出现 token
  assert.doesNotMatch(rendered, /token/)
  view.unmount()
})

test("a kind this page already retries inline is not announced twice", async () => {
  const store = memoryStorage({ [scopeKey]: JSON.stringify([
    entry("replay-order", { idempotency_key: "k1" }), entry("dataset-job", { idempotency_key: "k2" }),
  ]) })
  const view = mount({ storage: store, ignore: (kind) => kind.startsWith("replay-") })
  await view.ready()
  const rendered = text(view.render())
  assert.match(rendered, /1\s*次/)
  assert.deepEqual(links(view.render()), ["/factor-research"])
  view.unmount()
})

test("an unregistered kind is still announced instead of being silently dropped", async () => {
  const store = memoryStorage({ [scopeKey]: JSON.stringify([entry("brand-new-write", { idempotency_key: "k1" })]) })
  const view = mount({ storage: store })
  await view.ready()
  const rendered = text(view.render())
  assert.match(rendered, /brand-new-write/)
  assert.deepEqual(links(view.render()), [])
  view.unmount()
})

test("another login scope's entries never surface here", async () => {
  const store = memoryStorage({ "stock-pending-writes:someone-else:live": JSON.stringify([entry("replay-order")]) })
  const view = mount({ storage: store })
  await view.ready()
  assert.equal(view.render(), null)
  view.unmount()
})

test("a corrupt or non-array registry cannot crash the banner", async () => {
  for (const raw of ["not json", '{"kind":"dataset-job"}', "null"]) {
    const view = mount({ storage: memoryStorage({ [scopeKey]: raw }) })
    await view.ready()
    assert.equal(view.render(), null)
    view.unmount()
  }
})
