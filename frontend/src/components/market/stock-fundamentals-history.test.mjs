import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import vm from "node:vm"
import ts from "typescript"

const elements = (tree) => Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : []
const text = (tree) => Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree)

function mount() {
  const slots = [], effects = [], calls = []
  let cursor = 0, props = { code: "600001" }, auth = { accessToken: "captured-token", user: { id: "owner", market_type: "stock", trading_mode: "virtual" } }
  const dependencies = {
    react: {
      useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial; return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value }] },
      useRef(initial) { const index = cursor++; return slots[index] ??= { current: initial } },
      useEffect(callback, deps) { const index = cursor++; if (!(index in slots) || deps.some((value, position) => value !== slots[index].deps[position])) { slots[index]?.cleanup?.(); slots[index] = { deps }; effects.push(() => { slots[index].cleanup = callback() }) } },
    },
    "@/stores/auth": { useAuthStore: (selector) => selector(auth) },
    "@/lib/stock-fundamentals-api": { getStockFundamentals: (query, token, signal) => new Promise((resolve, reject) => calls.push({ query, token, signal, resolve, reject })) },
  }
  const source = readFileSync(new URL("./stock-fundamentals-history.tsx", import.meta.url), "utf8")
  const module = { exports: {} }, requireModule = createRequire(import.meta.url)
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText, { module, exports: module.exports, AbortController, Error, require: (name) => dependencies[name] ?? requireModule(name) })
  const render = () => { cursor = 0; const tree = module.exports.StockFundamentalsHistory(props); effects.splice(0).forEach((effect) => effect()); return tree }
  const button = (label) => elements(render()).find((item) => item.type === "button" && text(item) === label)
  const date = (value) => { elements(render()).find((item) => item.type === "input").props.onChange({ target: { value } }); render() }
  render(); date("2024-01-03")
  return { render, calls, button, date, kind(value) { elements(render()).find((item) => item.type === "select").props.onChange({ target: { value } }); render() },
    auth(patch) { auth = { ...auth, ...patch }; render() }, code(code) { props = { code }; render() }, settle: () => new Promise(setImmediate), unmount() { slots.forEach((slot) => slot?.cleanup?.()) } }
}

function payload(query, eligible = 21) {
  const rows = Array.from({ length: Math.max(0, Math.min(query.limit, eligible - query.offset)) }, (_, index) => {
    const day = new Date(Date.UTC(2023, 8, 30 - query.offset - index)).toISOString().slice(0, 10)
    return { report_date: day, publish_date: day, roe: -12, gross_margin: null, revenue_yoy: 2, netprofit_yoy: -3, eps: -1, bps: 2, netprofit: -100 }
  })
  return { query, instrument: { instrument_id: "600001.SSE" }, counts: { source_rows: eligible + 3, eligible_rows: eligible, unknown_publication: 1, invalid_dates: 1, later_rows: 1 }, rows, has_more: query.offset + rows.length < eligible, limitations: ["后续修订可能改变跨页结果，不是PIT证据"], history_version: "stock-fundamentals-history-v1", read_at: "2026-10-20T00:00:00Z" }
}

test("history is explicit, displays negative/null distinctly and pages within bounds", async () => {
  const page = mount(); assert.equal(page.calls.length, 0)
  page.button("读取历史").props.onClick(); assert.equal(page.calls[0].token, "captured-token")
  assert.match(text(page.render()), /正在读取/)
  page.calls[0].resolve(payload(page.calls[0].query)); await page.settle()
  assert.match(text(page.render()), /-12\.00%/); assert.match(text(page.render()), /—/)
  assert.match(text(page.render()), /缺公告.*1.*日期异常.*1/)
  assert.match(text(page.render()), /不是PIT/)
  page.button("下一页").props.onClick(); assert.equal(page.calls[1].query.offset, 20)
  page.calls[1].resolve(payload(page.calls[1].query)); await page.settle()
  assert.equal(page.button("下一页").props.disabled, true)
  page.button("上一页").props.onClick(); assert.equal(page.calls[2].query.offset, 0)
  page.unmount(); assert.equal(page.calls[2].signal.aborted, true)
})

test("identity mode market date and code changes abort and hide even late history", async () => {
  const page = mount(); page.button("读取历史").props.onClick()
  page.auth({ user: { id: "other", market_type: "stock", trading_mode: "live" }, accessToken: "new-token" })
  assert.equal(page.calls[0].signal.aborted, true)
  page.calls[0].resolve(payload(page.calls[0].query)); await page.settle()
  assert.doesNotMatch(text(page.render()), /-12\.00/)
  page.button("读取历史").props.onClick(); page.calls[1].resolve(payload(page.calls[1].query)); await page.settle()
  page.date("2024-01-02"); assert.doesNotMatch(text(page.render()), /-12\.00/)
  page.button("读取历史").props.onClick(); page.code("600002"); assert.equal(page.calls[2].signal.aborted, true)
  page.auth({ user: { id: "other", market_type: "futures", trading_mode: "virtual" } })
  assert.equal(page.button("读取历史").props.disabled, true)
  page.button("读取历史").props.onClick(); assert.equal(page.calls.length, 3)
})

test("failed history is not an empty success and manual retry or kind change stays bounded", async () => {
  const page = mount(); page.button("读取历史").props.onClick()
  page.calls[0].reject(Object.assign(new Error("读取失败"), { request_id: "a".repeat(32) })); await page.settle()
  assert.match(text(page.render()), /读取失败.*请求/)
  assert.doesNotMatch(text(page.render()), /没有符合截止条件/)
  page.button("重试读取").props.onClick(); assert.equal(page.calls.length, 2)
  page.calls[1].resolve(payload(page.calls[1].query, 0)); await page.settle()
  assert.match(text(page.render()), /没有符合截止条件/)
  page.kind("valuation"); assert.doesNotMatch(text(page.render()), /没有符合截止条件/)
  page.button("读取历史").props.onClick(); assert.equal(page.calls[2].query.kind, "valuation"); assert.equal(page.calls[2].query.offset, 0)
})
