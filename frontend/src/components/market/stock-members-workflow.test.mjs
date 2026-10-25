import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import vm from "node:vm"
import ts from "typescript"
import { selectStockBoards } from "./stock-board-order.ts"

const requireModule = createRequire(import.meta.url)
const output = ts.transpileModule(readFileSync(new URL("./stock-board-browser.tsx", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText
function elements(tree) { return Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : [] }
function text(tree) { return Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }
function rows(offset = 0, length = 50, total = 100) {
  return { as_of: "2026-10-16", total, has_more: offset + length < total,
    items: Array.from({ length }, (_, index) => {
      const code = String(offset + index + 1).padStart(6, "0")
      return { code, name: `证券${code}`, instrument_id: `${code}.SZ`, industry: "银行", close: 10, change_pct: 0, data_status: "ready" }
    }) }
}
function mount(response = (filters) => rows(filters.offset)) {
  const slots = [], effects = [], timers = new Map(), calls = [], selected = []
  let cursor = 0, timerId = 0
  const dependencies = {
    react: {
      useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial; return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value }] },
      useEffect(callback, deps) { const index = cursor++; if (!(index in slots) || deps.some((value, position) => value !== slots[index].deps[position])) { slots[index]?.cleanup?.(); slots[index] = { deps }; effects.push(() => { slots[index].cleanup = callback() }) } },
    },
    "@/lib/stock-market-api": { getStockMembersApi: async (filters, signal) => { calls.push({ filters, signal }); return response(filters) } },
    "./stock-board-order": { selectStockBoards },
    "@/components/market/stock-eod-status": { StockEodStatus: () => null },
    "@/lib/utils": { cn: (...values) => values.filter(Boolean).join(" ") },
  }
  const module = { exports: {} }
  vm.runInNewContext(output, { module, exports: module.exports, AbortController, Error,
    setTimeout(callback) { const id = ++timerId; timers.set(id, callback); return id }, clearTimeout(id) { timers.delete(id) },
    require: (name) => dependencies[name] ?? requireModule(name) })
  return {
    calls, selected,
    render() { cursor = 0; return module.exports.StockMembers({ industry: "银行", onSelect: (code) => selected.push(code) }) },
    async settle() { while (effects.length) effects.shift()(); for (const [id, callback] of timers) { timers.delete(id); callback() } await new Promise(setImmediate) },
    unmount() { slots.forEach((slot) => slot?.cleanup?.()) },
  }
}

test("members recover an emptied later page without showing an inverted range or changing filters", async () => {
  let reset = false
  const page = mount((filters) => filters.offset === 50 ? rows(50, 0, 20) : reset ? rows(0, 20, 20) : rows())
  page.render(); await page.settle()
  elements(page.render()).find((item) => item.props?.["aria-label"] === "下一页股票").props.onClick()
  page.render(); await page.settle()
  const empty = page.render()
  assert.doesNotMatch(text(empty), /51–50/)
  assert.match(text(empty), /0\s*\/\s*20/)
  const first = elements(empty).find((item) => item.props?.["aria-label"] === "返回第一页股票")
  assert.ok(first, "a changed result set must have a direct way back to its first page")
  assert.match(text(empty), /当前页|第一页/)
  reset = true
  first.props.onClick(); page.render(); await page.settle()
  assert.match(text(page.render()), /1–20\s*\/\s*20/)
  assert.equal(page.calls.at(-1).filters.offset, 0)
  assert.equal(page.calls.at(-1).filters.industry, "银行")
})

test("fifty members retain natural row height in a scrolling page and readable horizontal columns", async () => {
  const page = mount(); page.render(); await page.settle()
  const tree = page.render()
  assert.match(tree.props.className, /shrink-0/)
  const table = elements(tree).find((item) => item.type === "table")
  assert.match(table.props.className, /min-w-\[40rem\]/)
  const wrapper = elements(tree).find((item) => item.type === "div" && [item.props?.children].flat().includes(table))
  assert.ok(wrapper)
  assert.doesNotMatch(wrapper.props.className, /flex-1|min-h-0/)
  assert.equal(elements(table).filter((item) => item.type === "tr").length, 51)
  assert.match(readFileSync(new URL("./stock-universe.tsx", import.meta.url), "utf8"), /overflow-auto/)
  assert.match(readFileSync(new URL("./stock-universe.tsx", import.meta.url), "utf8"), /min-h-\[20rem\]/)
})

test("members stop at the API offset boundary and disclose the need to narrow a large result", async () => {
  const page = mount((filters) => rows(filters.offset, 50, 11000))
  page.render(); await page.settle()
  for (let index = 0; index < 200; index++) {
    elements(page.render()).find((item) => item.props?.["aria-label"] === "下一页股票").props.onClick()
    page.render(); await page.settle()
  }
  const tree = page.render()
  assert.equal(page.calls.at(-1).filters.offset, 10000)
  assert.equal(elements(tree).find((item) => item.props?.["aria-label"] === "下一页股票").props.disabled, true)
  assert.match(text(tree), /分页上限|缩小.*筛选/)
  assert.equal(page.calls.some((item) => item.filters.offset > 10000), false)
})

test("members keep stock selection and cancel a superseded keyword read without showing its late rows", async () => {
  let finish
  const page = mount((filters) => filters.keyword === "旧查询" ? new Promise((resolve) => { finish = resolve }) : rows(0, 1, 1))
  page.render(); await page.settle()
  elements(page.render()).find((item) => item.type === "button" && text(item).includes("证券000001")).props.onClick()
  assert.deepEqual(page.selected, ["000001"])
  const input = () => elements(page.render()).find((item) => item.props?.["aria-label"] === "搜索股票代码或名称")
  input().props.onChange({ target: { value: "旧查询" } }); page.render(); await page.settle()
  const old = page.calls.at(-1)
  input().props.onChange({ target: { value: "新查询" } }); page.render(); await page.settle()
  finish(rows(50, 1, 51)); await new Promise(setImmediate)
  assert.equal(old.signal.aborted, true)
  assert.doesNotMatch(text(page.render()), /证券000051/)
  assert.match(text(page.render()), /证券000001/)
  page.unmount(); assert.equal(page.calls.at(-1).signal.aborted, true)
})
