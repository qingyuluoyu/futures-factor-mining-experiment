import assert from "node:assert/strict"
import test from "node:test"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import vm from "node:vm"
import ts from "typescript"

const viewFilename = new URL("./stock-market-view.tsx", import.meta.url)
const overviewFilename = new URL("./stock-market-overview.tsx", import.meta.url)
const requireModule = createRequire(import.meta.url)
const marketNavigation = await import("../../lib/stock-market-navigation.ts")
const EodStatus = () => null
const Browser = () => null
const Detail = () => null
const boards = {
  as_of: "2026-10-16", previous_date: "2026-10-15", source: "teajoin_eod", price_adjustment: "none", has_more: false,
  coverage: { instruments: 5558, quoted: 5548, comparable: 5548 }, total: 1,
  boards: [{ industry: "银行", count: 100, quoted_count: 20, comparable_count: 15, advancing: 10, declining: 3, flat: 2, change_pct: 1.23, leader: null }],
}
function elements(tree) { return Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : [] }
function text(tree) { return Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }

function mount({ symbol = null, industry = null, response = boards } = {}) {
  assert.ok(existsSync(viewFilename), "stock market view must be implemented")
  assert.ok(existsSync(overviewFilename), "stock market overview must be implemented")
  const slots = [], effects = [], boardsCalls = [], navigations = [], selections = []
  const controls = { response }
  let cursor = 0
  const dependencies = {
    react: {
      useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial; return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value }] },
      useEffect(callback, deps) { const index = cursor++; if (!(index in slots) || deps.some((value, position) => value !== slots[index].deps[position])) { slots[index]?.cleanup?.(); slots[index] = { deps }; effects.push(() => { slots[index].cleanup = callback() }) } },
    },
    "next/navigation": { useRouter: () => ({ push: (url, options) => navigations.push({ url, options }) }), useSearchParams: () => new URLSearchParams({ ...(symbol ? { symbol } : {}), ...(industry !== null ? { industry } : {}) }) },
    "@/lib/stock-market-navigation": marketNavigation,
    "@/lib/stock-market-api": { getStockBoardsApi: async (keyword, signal) => { boardsCalls.push({ keyword, signal }); return controls.response } },
    "@/components/market/stock-eod-status": { StockEodStatus: EodStatus },
    "./stock-board-browser": { StockBoardBrowser: Browser },
    "./stock-quote-detail": { StockQuoteDetail: Detail },
    "@/stores/stock-market": { useStockMarketStore: (selector) => selector({ selectStock: (code) => selections.push(code) }) },
  }
  const compile = (source) => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  // The shared overview is executed in the same context with the same stubbed reads, so a
  // page-level assertion about "one board read and one EOD read" observes the real component.
  const overviewModule = { exports: {} }
  vm.runInNewContext(compile(readFileSync(overviewFilename, "utf8")), { module: overviewModule, exports: overviewModule.exports, AbortController, Error, require: (name) => dependencies[name] ?? requireModule(name) })
  const Overview = overviewModule.exports.StockMarketOverview
  dependencies["./stock-market-overview"] = overviewModule.exports
  const module = { exports: {} }
  vm.runInNewContext(compile(readFileSync(viewFilename, "utf8")), { module, exports: module.exports, AbortController, Error, URLSearchParams, require: (name) => dependencies[name] ?? requireModule(name) })
  // 行情页把共享总览包在布局容器里，因此 draw 必须深入子节点调用总览，
  // 其它组件保持为元素，既有断言看到的仍是真实渲染结果。
  function draw(node) {
    if (Array.isArray(node)) return node.flatMap(draw)
    if (!node || typeof node !== "object" || !node.props) return node
    if (node.type === Overview) return draw(node.type(node.props))
    if (typeof node.props.children === "function") return node
    return { ...node, props: { ...node.props, children: draw(node.props.children) } }
  }
  return {
    boardsCalls, navigations, selections, Overview,
    render() { cursor = 0; return draw(module.exports.StockMarketView()) },
    async settle() { while (effects.length) effects.shift()(); await new Promise(setImmediate) },
    unmount() { slots.forEach((slot) => slot?.cleanup?.()) },
  }
}

test("the market view selects the stock from the URL and returns to the board list through the router", async () => {
  const page = mount({ symbol: "600001" }); const tree = page.render()
  const detail = elements(tree).find((item) => item.type === Detail)
  assert.ok(detail)
  assert.equal(detail.props.code, "600001")
  assert.equal(detail.key, "600001")
  // 原实现的保留语义：打开个股后板块工作区仍挂载但整体隐藏，不卸载、不重新读取。
  const workspace = elements(tree).find((item) => typeof item.props?.className === "string" && item.props.className.split(/\s+/).includes("hidden"))
  assert.ok(workspace, "opening a stock must keep the board workspace hidden instead of unmounting it")
  assert.ok(elements(workspace).some((item) => item.type === Browser))
  detail.props.onBack()
  assert.equal(page.navigations.length, 1)
  assert.equal(page.navigations[0].url, "/market")
  assert.equal(page.navigations[0].options.scroll, false)
})

test("a malformed symbol keeps the board browser instead of selecting a wrong stock", async () => {
  for (const symbol of ["60001", "6000011", "abcdef", "sh600001"]) {
    const page = mount({ symbol }); const tree = page.render()
    assert.equal(elements(tree).some((item) => item.type === Detail), false, `symbol ${symbol} must not open a detail`)
    assert.ok(elements(tree).find((item) => item.type === Browser), `symbol ${symbol} must keep the board browser`)
    assert.equal(page.navigations.length, 0)
  }
  const missing = mount(); assert.ok(elements(missing.render()).find((item) => item.type === Browser))
})

test("selecting a board member records the stock and pushes the deep link", async () => {
  const page = mount(); page.render(); await page.settle()
  const browser = elements(page.render()).find((item) => item.type === Browser)
  browser.props.onSelect("600001")
  assert.deepEqual(page.selections, ["600001"])
  assert.equal(page.navigations.length, 1)
  assert.equal(page.navigations[0].url, "/market?symbol=600001")
  assert.equal(page.navigations[0].options.scroll, false)
})

test("the market view issues exactly one board directory read and one EOD status read", async () => {
  const page = mount(); const tree = page.render(); await page.settle()
  assert.equal(page.boardsCalls.length, 1)
  assert.equal(page.boardsCalls[0].keyword, "")
  assert.equal(elements(page.render()).filter((item) => item.type === EodStatus).length, 1)
  const source = readFileSync(viewFilename, "utf8")
  assert.equal((source.match(/<StockMarketOverview/g) ?? []).length, 1)
  assert.doesNotMatch(source, /getStockBoardsApi|getStockEodStatusApi|getStockMembersApi/)
  assert.match(source, /params\.get\("symbol"\)/)
  assert.match(source, /stockMarketHref/)
})

test("the market view cancels the shared read when the route unmounts", async () => {
  const page = mount(); page.render(); await page.settle(); page.unmount()
  assert.equal(page.boardsCalls[0].signal.aborted, true)
})

test("industry deep links open the same directory scope and preserve it through stock detail and return", async () => {
  const page = mount({ industry: "软件 & 服务" }); page.render(); await page.settle()
  const browser = elements(page.render()).find((item) => item.type === Browser)
  assert.equal(browser.props.industry, "软件 & 服务")
  browser.props.onSelect("000001")
  let url = new URL(page.navigations.at(-1).url, "https://stock.invalid")
  assert.equal(url.searchParams.get("industry"), "软件 & 服务")
  assert.equal(url.searchParams.get("symbol"), "000001")
  const detailPage = mount({ industry: "软件 & 服务", symbol: "000001" })
  elements(detailPage.render()).find((item) => item.type === Detail).props.onBack()
  url = new URL(detailPage.navigations.at(-1).url, "https://stock.invalid")
  assert.equal(url.searchParams.get("industry"), "软件 & 服务")
  assert.equal(url.searchParams.has("symbol"), false)
  browser.props.onIndustryChange("银行")
  assert.equal(new URL(page.navigations.at(-1).url, "https://stock.invalid").searchParams.get("industry"), "银行")
})

test("invalid industry scope stops the stock browser read instead of widening the query", async () => {
  const page = mount({ industry: "行".repeat(33), symbol: "000001" })
  const tree = page.render(); await page.settle()
  assert.ok(elements(tree).some((item) => item.props?.role === "alert"))
  assert.equal(elements(tree).some((item) => item.type === Browser || item.type === Detail), false)
  assert.equal(page.boardsCalls.length, 0)
  assert.equal(page.selections.length, 0)
  const recover = elements(tree).find((item) => item.type === "button")
  recover.props.onClick()
  assert.equal(page.navigations.at(-1).url, "/market")
})

test("market keeps full provenance available in a closed disclosure so industry browsing is the primary content", async () => {
  const page = mount(); page.render(); await page.settle()
  const tree = page.render()
  assert.ok(elements(tree).some((item) => item.props?.["aria-label"] === "行情简要上下文"))
  const disclosure = elements(tree).find((item) => item.props?.["aria-label"] === "行情数据与同步详情")
  assert.ok(disclosure); assert.notEqual(disclosure.props.open, true)
  assert.ok(elements(tree).some((item) => item.type === Browser))
})
