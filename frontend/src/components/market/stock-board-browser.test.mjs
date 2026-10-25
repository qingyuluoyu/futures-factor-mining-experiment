import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import vm from "node:vm"
import { createRequire } from "node:module"
import ts from "typescript"
import { selectStockBoards } from "./stock-board-order.ts"

const source = readFileSync(new URL("./stock-board-browser.tsx", import.meta.url), "utf8")
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
const requireModule = createRequire(import.meta.url)
const EodStatus = () => null
function elements(tree) { return Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : [] }
function text(tree) { return Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }
const boards = { as_of: "2026-10-16", coverage: { instruments: 100, quoted: 30 }, total: 1, boards: [{ industry: "银行", count: 100, comparable_count: 20, advancing: 10, declining: 8, flat: 2, change_pct: 1, leader: null }] }
function mount(response = boards, initialProps = {}) {
  const slots = [], effects = []
  let cursor = 0
  const props = { ...initialProps }
  const industryChanges = []
  const dependencies = {
    react: {
      useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = initial; return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value }] },
      useEffect(callback, deps) { const index = cursor++; if (!(index in slots) || deps.some((value, position) => value !== slots[index].deps[position])) { slots[index]?.cleanup?.(); slots[index] = { deps }; effects.push(() => { slots[index].cleanup = callback() }) } },
    },
    "@/lib/stock-market-api": { getStockBoardsApi: async () => response, getStockMembersApi: async () => ({ items: [], total: 0, has_more: false }) },
    "./stock-board-order": { selectStockBoards },
    "@/components/market/stock-eod-status": { StockEodStatus: EodStatus },
    "@/lib/utils": { cn: (...values) => values.filter(Boolean).join(" ") },
  }
  const module = { exports: {} }
  // 行情页 = 共享行情总览 + 看板网格 + 成员。总览与看板在同一上下文、同一批桩读取里联渲染，
  // 因此下面每条既有断言仍然覆盖真实的一次目录读取、一份覆盖/审计与一个盘后状态。
  const overviewModule = { exports: {} }
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL("./stock-market-overview.tsx", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText, { module: overviewModule, exports: overviewModule.exports, AbortController, require: (name) => dependencies[name] ?? requireModule(name) })
  const Overview = overviewModule.exports.StockMarketOverview
  dependencies["@/components/market/stock-market-overview"] = overviewModule.exports
  vm.runInNewContext(output, { module, exports: module.exports, AbortController, require: (name) => dependencies[name] ?? requireModule(name) })
  const render = () => { cursor = 0; return Overview({ collapsible: true, ...props, onIndustryChange: (industry) => { industryChanges.push(industry); if (props.industry !== undefined) props.industry = industry }, children: (overview) => module.exports.StockBoardBrowser({ ...overview, onSelect: () => {} }) }) }
  return { render, props, industryChanges, Overview, Members: module.exports.StockMembers, async ready() { while (effects.length) effects.shift()(); await new Promise(setImmediate) } }
}

test("board status is always reachable and follows its existing refresh revision", () => {
  const page = mount()
  assert.equal(elements(page.render()).find((item) => item.type === EodStatus)?.props.refreshKey, 0)
  elements(page.render()).find((item) => item.props?.["aria-label"] === "刷新板块").props.onClick()
  assert.equal(elements(page.render()).find((item) => item.type === EodStatus)?.props.refreshKey, 1)
})

test("operational status is initially collapsed so industry browsing is not pushed below it", () => {
  const page = mount()
  const disclosure = elements(page.render()).find((item) => item.type === "details" && text(item).includes("盘后同步与影子归档状态"))
  assert.ok(disclosure)
  assert.notEqual(disclosure.props.open, true)
  assert.ok(elements(disclosure).some((item) => item.type === EodStatus))
  assert.ok(elements(disclosure).some((item) => item.type === "summary"))
})

test("industry cards fit six per desktop row and two or three on narrower screens", async () => {
  const page = mount(); page.render(); await page.ready()
  const card = elements(page.render()).find((item) => item.type === "button" && text(item).includes("银行"))
  assert.ok(card)
  const grids = elements(page.render()).filter((item) => item.props?.className?.includes("lg:grid-cols-6"))
  assert.ok(grids.length > 0)
  assert.ok(grids.some((item) => item.props.className.includes("grid-cols-2") && item.props.className.includes("sm:grid-cols-3")))
  assert.match(card.props.className, /min-w-0/)
})

test("compact board cards retain sample disclosure and industry member navigation", async () => {
  const page = mount(); page.render(); await page.ready()
  const tree = page.render()
  assert.match(text(tree), /不是官方行业指数/)
  assert.match(text(tree), /不填假行情/)
  elements(tree).find((item) => item.type === "button" && text(item).includes("银行")).props.onClick()
  const members = elements(page.render()).find((item) => item.type === page.Members)
  assert.equal(members.props.industry, "银行")
})

test("boards disclose actual EOD source, mismatches and expandable version evidence", async () => {
  const page = mount({ ...boards, source: "teajoin daily 盘后快照", source_row_count: 5567, missing_codes: Array.from({ length: 10 }, (_, index) => String(index + 1).padStart(6, "0")), unknown_codes: Array.from({ length: 19 }, (_, index) => String(index + 999000)), data_version: "abcdef012345".repeat(5), collected_at: "2026-10-18T04:00:00+00:00", reference_price: "vendor_ex_rights_pre_close", license_scope: "operator_configured_account_not_redistribution_certified" })
  page.render(); await page.ready()
  const tree = page.render()
  assert.match(text(tree), /teajoin daily 盘后快照/)
  assert.match(text(tree), /来源行数\s*5567/)
  assert.match(text(tree), /缺行情\s*10/)
  assert.match(text(tree), /目录未匹配\s*19/)
  assert.match(text(tree), /非实时/)
  const details = elements(tree).find((element) => element.type === "details")
  assert.ok(details)
  assert.match(text(details), /000001/)
  assert.match(text(details), /999000/)
  assert.match(text(details), /abcdef012345/)
  assert.match(text(details), /2026-10-18T04:00:00\+00:00/)
})

test("legacy board responses never invent EOD counts or snapshot versions", async () => {
  const page = mount({ ...boards, source: "本地授权导入库", price_adjustment: "qfq" }); page.render(); await page.ready()
  assert.match(text(page.render()), /本地授权导入库/)
  assert.doesNotMatch(text(page.render()), /来源行数|目录未匹配\s*0|缺行情\s*0|采集时间|版本/)
})

test("sector controls sort both directions, keep null last and retain zero in comparable filtering", async () => {
  const page = mount({ ...boards, boards: [
    { ...boards.boards[0], industry: "无报价", change_pct: null, comparable_count: 0 },
    { ...boards.boards[0], industry: "平盘", change_pct: 0 },
    { ...boards.boards[0], industry: "上涨", change_pct: 2 },
    { ...boards.boards[0], industry: "下跌", change_pct: -1 },
  ] })
  page.render(); await page.ready()
  const ordering = elements(page.render()).find((item) => item.props?.["aria-label"] === "板块排序")
  assert.ok(ordering)
  ordering.props.onChange({ target: { value: "change_pct" } })
  const names = () => elements(page.render()).filter((item) => item.props?.["data-stock-board"]).map((item) => item.props["data-stock-board"])
  assert.deepEqual(names(), ["上涨", "平盘", "下跌", "无报价"])
  elements(page.render()).find((item) => item.props?.["aria-label"] === "板块排序方向").props.onClick()
  assert.deepEqual(names(), ["下跌", "平盘", "上涨", "无报价"])
  elements(page.render()).find((item) => item.props?.["aria-label"] === "仅显示可比板块").props.onChange({ target: { checked: true } })
  assert.deepEqual(names(), ["下跌", "平盘", "上涨"])
  assert.match(text(page.render()), /显示 3 \/ 4 个板块/)
  elements(page.render()).find((item) => item.props?.["aria-label"] === "搜索行业板块").props.onChange({ target: { value: "平" } })
  assert.deepEqual(names(), ["平盘"])
})

test("refreshing the selected sector refreshes its members without replacing the query instance", async () => {
  const page = mount(); page.render(); await page.ready()
  elements(page.render()).find((item) => item.type === "button" && text(item).includes("银行")).props.onClick()
  const before = elements(page.render()).find((item) => item.type === page.Members)
  elements(page.render()).find((item) => item.props?.["aria-label"] === "刷新板块").props.onClick()
  const after = elements(page.render()).find((item) => item.type === page.Members)
  assert.equal(after.key, before.key)
  assert.equal(after.props.refreshKey, 1)
})

test("a controlled industry immediately opens its members and returning clears the parent selection", async () => {
  const page = mount(boards, { industry: "银行" }); page.render(); await page.ready()
  let tree = page.render()
  const members = elements(tree).find((item) => item.type === page.Members)
  assert.ok(members)
  assert.equal(members.props.industry, "银行")
  assert.equal(elements(tree).some((item) => item.props?.["data-stock-board"]), false)
  elements(tree).find((item) => item.type === "button" && text(item) === "← 板块总览").props.onClick()
  tree = page.render()
  assert.deepEqual(page.industryChanges, [""])
  assert.equal(elements(tree).some((item) => item.type === page.Members), false)
  assert.ok(elements(tree).some((item) => item.props?.["data-stock-board"] === "银行"))
})

test("empty industry keeps sector overview until all stocks is explicitly requested", async () => {
  const page = mount(boards, { industry: "" }); page.render(); await page.ready()
  assert.equal(elements(page.render()).some((item) => item.type === page.Members), false)
  elements(page.render()).find((item) => item.type === "button" && text(item) === "全部股票").props.onClick()
  assert.equal(elements(page.render()).find((item) => item.type === page.Members).props.industry, "")
  elements(page.render()).find((item) => item.type === "button" && text(item) === "← 板块总览").props.onClick()
  assert.equal(elements(page.render()).some((item) => item.type === page.Members), false)
})

test("board browser has natural height so the shared overview controls vertical scrolling", async () => {
  const page = mount(); page.render(); await page.ready()
  const tree = page.render()
  const boardRoot = elements(tree).find((item) => item.type === "div" && elements(item).some((child) => child.type === "header" && text(child).includes("行业板块")) && /p-3/.test(item.props?.className ?? ""))
  assert.ok(boardRoot)
  assert.doesNotMatch(boardRoot.props.className, /h-full|overflow|flex-1/)
  assert.match(boardRoot.props.className, /shrink-0/)
})

test("external industry changes replace an all-stock view and clearing the industry returns to sectors", async () => {
  const page = mount(boards, { industry: "" }); page.render(); await page.ready()
  elements(page.render()).find((item) => item.type === "button" && text(item) === "全部股票").props.onClick()
  page.render(); await page.ready()
  page.props.industry = "银行"
  page.render(); await page.ready()
  assert.equal(elements(page.render()).find((item) => item.type === page.Members).props.industry, "银行")
  page.props.industry = ""
  assert.equal(elements(page.render()).some((item) => item.type === page.Members), false)
  assert.ok(elements(page.render()).some((item) => item.props?.["data-stock-board"] === "银行"))
})
