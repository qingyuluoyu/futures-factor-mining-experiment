import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import vm from "node:vm"
import { createRequire } from "node:module"
import { jsx } from "react/jsx-runtime"
import ts from "typescript"

function mount({ enabled = true, detailFails = false, artifacts = true, bookmarks = false } = {}) {
  const source = readFileSync(new URL("./stock-research-history.tsx", import.meta.url), "utf8")
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const slots = [], effects = [], calls = { list: [], bookmarks: [], details: 0, results: 0, detailIds: [] }
  let cursor = 0, token = "token", revision = 1
  const failed = { id: "11111111-1111-4111-8111-111111111111", title: "失败研究", kind: "analysis", status: "failed", progress: 0, error: { code: "missing_price", message: "持仓缺价" } }
  const controls = { detailFails, nextJob: failed, deferredJob: null, items: [failed], resultFails: false, listFailure: null, deferredList: null, detailSignal: null, detailFailure: null, resultFailure: null, deferredResult: null, total: 21, hasMore: true }
  const wrapper = (props) => jsx("div", { children: props.children })
  const deps = {
    react: { useState(initial) { const slot = cursor++; if (!(slot in slots)) slots[slot] = initial; return [slots[slot], (value) => { slots[slot] = typeof value === "function" ? value(slots[slot]) : value }] }, useRef(initial) { const slot = cursor++; if (!(slot in slots)) slots[slot] = { current: initial }; return slots[slot] }, useEffect(effect, deps) { const slot = cursor++; if (!(slot in slots) || deps.some((value, index) => value !== slots[slot].deps[index])) { slots[slot]?.cleanup?.(); slots[slot] = { deps }; effects.push(() => { slots[slot].cleanup = effect() }) } }, useCallback: (callback) => callback },
    "next/link": { default: wrapper },
    "@/stores/auth": { useAuthStore: Object.assign((selector) => selector({ user: { id: "user", market_type: "stock" }, accessToken: token, sessionRevision: revision }), { getState: () => ({ user: { id: "user", market_type: "stock" }, accessToken: token, sessionRevision: revision }) }) },
    "@/lib/stock-research-api": { stockResearchScope: () => "user:virtual", getStockReportBookmarkAvailability: async () => ({ enabled: bookmarks, reason: null }), listBookmarkedStockResearchJobs: async (query) => { calls.bookmarks.push(query); return { items: controls.items, total: 1, has_more: false } }, getStockResearchAvailability: async () => ({ enabled, accepting: enabled, saved_report_artifacts: artifacts, reason: null }), listStockResearchJobs: async (query) => { calls.list.push(query); if (controls.listFailure) throw controls.listFailure; return controls.deferredList ?? { items: controls.items, total: controls.total, has_more: controls.hasMore } }, getStockResearchJob: async (_id, _token, signal) => { calls.details++; calls.detailIds.push(_id); controls.detailSignal = signal; if (controls.detailFailure) throw controls.detailFailure; if (controls.detailFails) throw Object.assign(new Error("不存在"), { status: 404 }); return controls.deferredJob ?? { ...controls.nextJob, params: { factor_id: "ret_20d" }, data_manifest: {} } }, getStockResearchResult: async () => { calls.results++; if (controls.resultFailure) throw controls.resultFailure; if (controls.resultFails) throw new Error("旧报告字段无法显示"); return controls.deferredResult ?? {} } },
    "./research-report": { ResearchReport: wrapper },
    "./research-job-panel": { ResearchJobMetadata: (props) => jsx("div", props), researchStatusLabels: { pending: "排队中", running: "运行中", completed: "已完成", failed: "失败", cancelled: "已取消", expired: "已过期" } },
    "./research-report-actions": { ResearchReportActions: (props) => jsx("div", { ...props, "data-actions": true }) },
    "./research-comparison-panel": { ResearchComparisonPanel: (props) => jsx("div", { ...props, "data-comparison": true }) },
    "./research-report-bookmark": { ResearchReportBookmark: (props) => jsx("div", { ...props, "data-bookmark": true }) },
  }
  const module = { exports: {} }, requireModule = createRequire(import.meta.url)
  vm.runInNewContext(output, { module, exports: module.exports, require: (name) => deps[name] ?? requireModule(name), AbortController, Error })
  function render() { cursor = 0; return module.exports.StockResearchHistoryWorkspace({ token: "token", scope: "user:virtual" }) }
  render()
  return { render, calls, controls, async ready() { effects.splice(0).forEach((effect) => effect()); await settle() }, switchUser() { token = "other-token" }, renewSession() { revision++ }, unmount() { slots.forEach((slot) => slot?.cleanup?.()) } }
}
function elements(tree) { if (Array.isArray(tree)) return tree.flatMap(elements); return tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : [] }
function text(tree) { if (Array.isArray(tree)) return tree.map(text).join(""); return tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }
function button(tree, label) { const result = elements(tree).find((element) => element.type === "button" && text(element).includes(label)); assert.ok(result); return result }
const settle = () => new Promise((resolve) => setImmediate(resolve))

test("restoring a saved backtest opens the portfolio page instead of the analysis page", async () => {
  const page = mount(); await page.ready()
  page.controls.nextJob = { ...page.controls.nextJob, kind: "backtest" }
  await button(page.render(), "失败研究").props.onClick(); await settle()
  const link = elements(page.render()).find((node) => text(node) === "恢复配置到新研究")
  assert.ok(link)
  assert.equal(link.props.href, `/backtest?restore=${page.controls.nextJob.id}`)
})

test("restoring a saved analysis keeps the factor research page", async () => {
  const page = mount(); await page.ready()
  await button(page.render(), "失败研究").props.onClick(); await settle()
  const link = elements(page.render()).find((node) => text(node) === "恢复配置到新研究")
  assert.equal(link.props.href, `/factor-research?restore=${page.controls.nextJob.id}`)
})

test("failed history detail retries the original job after list filters change without reloading the list", async () => {
  const page = mount(); await page.ready()
  const original = page.controls.nextJob.id
  page.controls.detailFailure = new Error("报告暂时不可用")
  await button(page.render(), "失败研究").props.onClick(); await settle()
  assert.equal(page.calls.details, 1)
  assert.match(text(page.render()), /报告暂时不可用/)
  const filter = elements(page.render()).find((node) => node.props?.["aria-label"] === "任务状态")
  filter.props.onChange({ target: { value: "completed" } }); page.render(); await page.ready()
  const listCount = page.calls.list.length
  page.controls.detailFailure = null
  await button(page.render(), "重试报告详情").props.onClick(); await settle()
  assert.deepEqual(page.calls.detailIds, [original, original])
  assert.equal(page.calls.list.length, listCount)
  assert.equal(page.calls.list.at(-1).status, "completed")
  assert.doesNotMatch(text(page.render()), /报告暂时不可用/)
})

test("closing a pending history detail aborts it and prevents its late completion from opening a result", async () => {
  const page = mount(); await page.ready()
  let finish
  page.controls.deferredJob = new Promise((resolve) => { finish = resolve })
  const request = button(page.render(), "失败研究").props.onClick()
  const signal = page.controls.detailSignal
  button(page.render(), "关闭报告详情").props.onClick()
  assert.equal(signal.aborted, true)
  finish({ ...page.controls.nextJob, status: "completed", has_result: true })
  await request; await settle()
  assert.equal(page.calls.results, 0)
  assert.doesNotMatch(text(page.render()), /加载报告详情|刷新此任务/)
  assert.equal(elements(page.render()).some((node) => node.props?.job), false)
  assert.equal(page.calls.list.length, 1)
})

test("closing a loaded report retains comparison and current page without fetching or cancelling the job", async () => {
  const page = mount(); page.controls.nextJob = { ...page.controls.nextJob, status: "completed", has_result: true }
  page.controls.items = [page.controls.nextJob]; await page.ready()
  elements(page.render()).find((node) => node.props?.["aria-label"] === `选择对照 ${page.controls.nextJob.id}`).props.onChange()
  await button(page.render(), "失败研究").props.onClick(); await settle()
  const resultCount = page.calls.results
  button(page.render(), "下一页").props.onClick(); page.render(); await page.ready()
  const listCount = page.calls.list.length
  button(page.render(), "关闭报告详情").props.onClick()
  assert.match(text(page.render()), /第 2 页/)
  assert.equal(elements(page.render()).find((node) => node.props?.leftId)?.props.leftId, page.controls.nextJob.id)
  assert.equal(elements(page.render()).some((node) => node.props?.job || node.props?.["data-actions"]), false)
  assert.equal(page.calls.list.length, listCount)
  assert.equal(page.calls.results, resultCount)
})

test("an emptied later history page can return to page one using the same type and status filters", async () => {
  const page = mount(); await page.ready()
  elements(page.render()).find((node) => node.props?.["aria-label"] === "研究类型").props.onChange({ target: { value: "analysis" } }); page.render(); await page.ready()
  page.controls.items = []; page.controls.total = 1; page.controls.hasMore = false
  button(page.render(), "下一页").props.onClick(); page.render(); await page.ready()
  assert.match(text(page.render()), /当前页没有|当前页.*变化/)
  assert.doesNotMatch(text(page.render()), /当前筛选条件下暂无/)
  page.controls.items = [page.controls.nextJob]
  button(page.render(), "返回第一页报告").props.onClick(); page.render(); await page.ready()
  assert.equal(page.calls.list.at(-1).offset, 0)
  assert.equal(page.calls.list.at(-1).kind, "analysis")
  assert.match(text(page.render()), /第 1 页/)
})

test("inaccessible reports have no retry action and an old-session retry cannot issue a request", async () => {
  const missing = mount({ detailFails: true }); await missing.ready()
  await button(missing.render(), "失败研究").props.onClick(); await settle()
  assert.equal(elements(missing.render()).some((node) => node.type === "button" && text(node) === "重试报告详情"), false)
  const page = mount(); await page.ready(); page.controls.detailFailure = new Error("暂时不可用")
  await button(page.render(), "失败研究").props.onClick(); await settle()
  const retry = button(page.render(), "重试报告详情")
  page.renewSession(); page.controls.detailFailure = null
  retry.props.onClick(); await settle()
  assert.equal(page.calls.details, 1)
})

test("history does not offer retry for deterministic invalid-session permission or parameter failures", async () => {
  for (const status of [400, 401, 403, 404, 422]) {
    const page = mount(); await page.ready()
    page.controls.detailFailure = Object.assign(new Error("请求不可访问或参数无效"), { status })
    await button(page.render(), "失败研究").props.onClick(); await settle()
    assert.equal(page.calls.details, 1)
    assert.equal(elements(page.render()).some((node) => node.type === "button" && text(node) === "重试报告详情"), false)
    assert.equal(elements(page.render()).some((node) => node.props?.["data-actions"]), false)
    page.unmount()
  }
})

test("result permission loss clears selected metadata and export controls instead of leaving a retryable report", async () => {
  const page = mount(); await page.ready()
  page.controls.nextJob = { ...page.controls.nextJob, status: "completed", has_result: true }
  page.controls.resultFailure = Object.assign(new Error("无权读取结果"), { status: 403 })
  await button(page.render(), "失败研究").props.onClick(); await settle()
  assert.match(text(page.render()), /无权读取结果/)
  assert.equal(page.calls.results, 1)
  assert.equal(elements(page.render()).some((node) => node.props?.job || node.props?.["data-actions"]), false)
  assert.equal(elements(page.render()).some((node) => node.type === "button" && text(node) === "重试报告详情"), false)
})

test("closing during result loading discards the result even when its transport resolves after abort", async () => {
  const page = mount(); await page.ready()
  let finish
  page.controls.nextJob = { ...page.controls.nextJob, status: "completed", has_result: true }
  page.controls.deferredResult = new Promise((resolve) => { finish = resolve })
  const request = button(page.render(), "失败研究").props.onClick(); await settle()
  assert.equal(page.calls.results, 1)
  const signal = page.controls.detailSignal
  button(page.render(), "关闭报告详情").props.onClick()
  finish({}); await request; await settle()
  assert.equal(signal.aborted, true)
  assert.equal(elements(page.render()).some((node) => node.props?.job || node.props?.["data-actions"]), false)
  assert.doesNotMatch(text(page.render()), /加载报告详情|刷新此任务|关闭报告详情/)
})

test("disabled history shows unavailable and never fetches fake reports", async () => {
  const page = mount({ enabled: false }); await page.ready()
  assert.match(text(page.render()), /未启用/)
  assert.equal(page.calls.list.length, 0)
})

test("history lists bounded real jobs and failure detail without requesting a result", async () => {
  const page = mount(); await page.ready()
  assert.equal(page.calls.list[0].limit, 20)
  assert.equal(page.calls.list[0].offset, 0)
  assert.match(text(page.render()), /失败研究/)
  await button(page.render(), "失败研究").props.onClick()
  await settle()
  assert.equal(page.calls.details, 1)
  assert.equal(page.calls.results, 0)
  assert.match(text(page.render()), /持仓缺价/)
  assert.equal(button(page.render(), "下一页").props.disabled, false)
})

test("inaccessible details clear previous report and surface 404", async () => {
  const page = mount({ detailFails: true }); await page.ready()
  await button(page.render(), "失败研究").props.onClick(); await settle()
  assert.match(text(page.render()), /不存在/)
  assert.equal(page.calls.results, 0)
})

test("history route keeps futures report and chooses stock history explicitly", () => {
  const source = readFileSync(new URL("../../app/(main)/history/page.tsx", import.meta.url), "utf8")
  assert.match(source, /market_type === "stock"/)
  assert.match(source, /StockResearchHistory/)
  assert.match(source, /SuperFactorPage/)
})

test("history retrieves successful results only after the authorized completed detail", async () => {
  const page = mount(); await page.ready()
  page.controls.nextJob = { id: "11111111-1111-4111-8111-111111111111", kind: "analysis", status: "completed", has_result: true }
  await button(page.render(), "失败研究").props.onClick(); await settle()
  assert.equal(page.calls.results, 1)
})

test("late history details after session switch cannot request or display another result", async () => {
  const page = mount(); await page.ready()
  let resolve
  page.controls.deferredJob = new Promise((finish) => { resolve = finish })
  const viewing = button(page.render(), "失败研究").props.onClick()
  page.switchUser()
  resolve({ id: "11111111-1111-4111-8111-111111111111", kind: "analysis", status: "completed", has_result: true })
  await viewing; await settle()
  assert.equal(page.calls.results, 0)
})

test("completed report details expose authenticated export and explicit restore link", async () => {
  const page = mount(); await page.ready()
  page.controls.nextJob = { id: "11111111-1111-4111-8111-111111111111", kind: "analysis", status: "completed", has_result: true }
  await button(page.render(), "失败研究").props.onClick(); await settle()
  assert.ok(elements(page.render()).find((node) => node.props?.href === `/factor-research?restore=${page.controls.nextJob.id}`))
  const actions = elements(page.render()).find((node) => node.props?.jobId === page.controls.nextJob.id && node.props?.token === "token")
  assert.ok(actions)
})

test("history comparison retains at most two same-kind completed choices across pages", async () => {
  const page = mount()
  const jobs = ["1", "2", "3"].map((digit) => ({ id: `${digit.repeat(8)}-${digit.repeat(4)}-4${digit.repeat(3)}-8${digit.repeat(3)}-${digit.repeat(12)}`, title: `已完成${digit}`, kind: "analysis", status: "completed", has_result: true }))
  page.controls.items = [...jobs, { ...jobs[0], id: "backtest", kind: "backtest" }]
  await page.ready()
  const choice = (id) => elements(page.render()).find((node) => node.type === "input" && node.props["aria-label"] === `选择对照 ${id}`)
  assert.ok(choice(jobs[0].id))
  choice(jobs[0].id).props.onChange()
  choice(jobs[1].id).props.onChange()
  assert.equal(choice(jobs[2].id).props.disabled, true)
  assert.equal(choice("backtest").props.disabled, true)
  button(page.render(), "下一页").props.onClick()
  page.render(); await page.ready()
  const panel = elements(page.render()).find((node) => node.props?.leftId)
  assert.equal(panel.props.leftId, jobs[0].id)
  assert.equal(panel.props.rightId, jobs[1].id)
  panel.props.onClear()
  assert.equal(choice(jobs[2].id).props.disabled, false)
})

test("same-token new login revision discards late history details", async () => {
  const page = mount(); await page.ready()
  let resolve; page.controls.deferredJob = new Promise((finish) => { resolve = finish })
  const request = button(page.render(), "失败研究").props.onClick()
  page.renewSession()
  resolve({ id: "11111111-1111-4111-8111-111111111111", status: "completed", has_result: true })
  await request; await settle()
  assert.equal(page.calls.results, 0)
})

test("saved legacy report can still export when current visual result schema rejects it", async () => {
  const page = mount(); await page.ready()
  page.controls.nextJob = { id: "11111111-1111-4111-8111-111111111111", kind: "analysis", status: "completed", has_result: true }
  page.controls.resultFails = true
  await button(page.render(), "失败研究").props.onClick(); await settle()
  assert.match(text(page.render()), /旧报告字段无法显示/)
  assert.ok(elements(page.render()).find((node) => node.props?.jobId === page.controls.nextJob.id && node.props?.token === "token"))
})

test("old deployment hides unavailable export and comparison controls but keeps report reads", async () => {
  const page = mount({ artifacts: false }); await page.ready()
  page.controls.nextJob = { id: "11111111-1111-4111-8111-111111111111", kind: "analysis", status: "completed", has_result: true }
  await button(page.render(), "失败研究").props.onClick(); await settle()
  assert.equal(page.calls.results, 1)
  assert.equal(elements(page.render()).some((node) => node.props?.jobId), false)
  assert.equal(elements(page.render()).some((node) => node.type === "input" && node.props?.type === "checkbox"), false)
  assert.match(text(page.render()), /尚未部署报告导出与对照/)
})

test("history status filter combines kind and resets page without clearing comparison", async () => {
  const page = mount(); await page.ready()
  button(page.render(), "下一页").props.onClick(); page.render(); await page.ready()
  const status = elements(page.render()).find((node) => node.type === "select" && node.props["aria-label"] === "任务状态")
  assert.ok(status, "all six server states must be selectable")
  assert.equal(elements(status.props.children).filter((node) => node.type === "option").length, 7)
  status.props.onChange({ target: { value: "failed" } }); page.render(); await page.ready()
  assert.deepEqual(JSON.parse(JSON.stringify(page.calls.list.at(-1))), { limit: 20, offset: 0, status: "failed" })
  const kind = elements(page.render()).find((node) => node.type === "select" && node.props["aria-label"] === "研究类型")
  kind.props.onChange({ target: { value: "analysis" } }); page.render(); await page.ready()
  assert.deepEqual(JSON.parse(JSON.stringify(page.calls.list.at(-1))), { limit: 20, offset: 0, status: "failed", kind: "analysis" })
})

test("changing list page must not abort or strand an independent detail request", async () => {
  const page = mount(); await page.ready()
  let finish
  page.controls.deferredJob = new Promise((resolve) => { finish = resolve })
  const request = button(page.render(), "失败研究").props.onClick()
  const signal = page.controls.detailSignal
  button(page.render(), "下一页").props.onClick(); page.render(); await page.ready()
  assert.equal(signal.aborted, false, "pagination is not detail cancellation")
  finish({ ...page.controls.nextJob, title: "详情已加载" }); await request; await settle()
  assert.doesNotMatch(text(page.render()), /加载报告详情/)
  assert.ok(elements(page.render()).find((node) => node.props?.job?.title === "详情已加载"))
})

test("new list load and failure never leave previous-page report buttons clickable", async () => {
  const page = mount(); await page.ready()
  let finish
  page.controls.deferredList = new Promise((resolve) => { finish = resolve })
  button(page.render(), "下一页").props.onClick(); page.render(); await page.ready()
  assert.equal(elements(page.render()).some((node) => node.type === "button" && text(node).includes("失败研究")), false)
  finish({ items: [], total: 21, has_more: false }); await settle()
  page.controls.deferredList = null; page.controls.listFailure = new Error("查询失败")
  button(page.render(), "刷新历史报告").props.onClick(); await settle()
  assert.match(text(page.render()), /查询失败/)
  assert.doesNotMatch(text(page.render()), /暂无研究任务/)
})

test("unmount aborts an independent detail and forbids late result fetch", async () => {
  const page = mount(); await page.ready()
  let finish
  page.controls.deferredJob = new Promise((resolve) => { finish = resolve })
  const request = button(page.render(), "失败研究").props.onClick()
  const signal = page.controls.detailSignal
  page.unmount()
  assert.equal(signal.aborted, true)
  finish({ ...page.controls.nextJob, status: "completed", has_result: true }); await request; await settle()
  assert.equal(page.calls.results, 0)
})

test("status filtering preserves explicitly selected comparison reports", async () => {
  const page = mount()
  const completed = { ...page.controls.nextJob, status: "completed", has_result: true }
  page.controls.items = [completed]
  await page.ready()
  elements(page.render()).find((node) => node.type === "input").props.onChange()
  page.controls.items = []
  const status = elements(page.render()).find((node) => node.type === "select" && node.props["aria-label"] === "任务状态")
  status.props.onChange({ target: { value: "failed" } }); page.render(); await page.ready()
  const panel = elements(page.render()).find((node) => node.props?.leftId)
  assert.equal(panel.props.leftId, completed.id)
  assert.match(text(page.render()), /暂无研究任务/)
})

test("bookmark filter uses its scoped endpoint and resets page while preserving status", async () => {
  const page = mount({ bookmarks: true }); await page.ready()
  button(page.render(), "下一页").props.onClick(); page.render(); await page.ready()
  const checkbox = elements(page.render()).find((node) => node.type === "input" && node.props["aria-label"] === "仅收藏报告")
  assert.ok(checkbox)
  checkbox.props.onChange({ target: { checked: true } }); page.render(); await page.ready()
  assert.deepEqual(JSON.parse(JSON.stringify(page.calls.bookmarks.at(-1))), { limit: 20, offset: 0 })
  const status = elements(page.render()).find((node) => node.type === "select" && node.props["aria-label"] === "任务状态")
  status.props.onChange({ target: { value: "completed" } }); page.render(); await page.ready()
  assert.equal(page.calls.bookmarks.at(-1).status, "completed")
})

test("bookmark controls are capability gated and successful report gets the correct identity", async () => {
  const old = mount(); await old.ready()
  assert.equal(elements(old.render()).some((node) => node.props?.["aria-label"] === "仅收藏报告"), false)
  const page = mount({ bookmarks: true }); await page.ready()
  page.controls.nextJob = { ...page.controls.nextJob, status: "completed", has_result: true }
  await button(page.render(), "失败研究").props.onClick(); await settle()
  const action = elements(page.render()).find((node) => node.props?.jobId && typeof node.props?.onChanged === "function")
  assert.ok(action)
  assert.equal(action.props.token, "token")
  assert.equal(action.props.scope, "user:virtual")
  assert.equal(action.props.jobId, page.controls.nextJob.id)
})
