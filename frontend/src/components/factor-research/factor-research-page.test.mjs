import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import { jsx } from "react/jsx-runtime"
import ts from "typescript"
import * as researchApi from "../../lib/stock-research-api.ts"

const requireModule = createRequire(import.meta.url)
const source = readFileSync(new URL("./factor-research-workspace.tsx", import.meta.url), "utf8")
const output = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText

const coverage = { catalog_count: 5558, available: { securities: 300, first_trade_date: "2026-01-01", last_trade_date: "2026-09-30" }, recommended: { start_date: "2026-07-01", end_date: "2026-09-30" }, selected: { securities: 300, observed_dates: 61, price_rows: 18000, missing_price_cells: 300, panel_cells: 164700 }, limits: { max_panel_cells: 2000000 }, status: "within_limits", warnings: [] }

function mountResearch({ catalogFails = false, statusFails = false, analysisFails = false, enabled = true, accepting = true, artifacts = true, selective = true, job = null, deferredCoverage = null, initialTab, title, catalog, view } = {}) {
  const slots = []
  const effects = []
  const timers = new Map()
  let cursor = 0
  let timerId = 0
  const controls = { catalogFails, statusFails, analysisFails, job, result: null, coverage, coverageFails: false, deferredCoverage, busy: false, blocked: false, recoveryPending: false }
  const calls = { analysis: [], backtest: [], catalog: 0, status: 0, coverage: [], delays: [] }
  const wrapper = (props) => jsx("div", { children: props.children })
  const dependencies = {
    react: {
      Suspense: wrapper,
      useState: (initial) => {
        const position = cursor++
        if (!(position in slots)) slots[position] = typeof initial === "function" ? initial() : initial
        return [slots[position], (value) => { slots[position] = typeof value === "function" ? value(slots[position]) : value }]
      },
      useRef: (initial) => { const position = cursor++; if (!(position in slots)) slots[position] = { current: initial }; return slots[position] },
      useCallback: (callback, deps) => { const position = cursor++; if (!(position in slots) || deps.some((value, index) => value !== slots[position].deps[index])) slots[position] = { value: callback, deps }; return slots[position].value },
      useEffect: (callback, deps) => {
        const position = cursor++
        if (!(position in slots) || deps.some((value, index) => value !== slots[position].deps[index])) {
          slots[position]?.cleanup?.()
          slots[position] = { deps }
          effects.push(() => { slots[position].cleanup = callback() })
        }
      },
    },
    "lucide-react": { FlaskConical: () => null, LineChart: () => null, Loader2: () => null },
    "@/components/ui/tabs": { Tabs: wrapper, TabsList: wrapper, TabsTrigger: wrapper, TabsContent: wrapper },
    "@/components/factor-research/mini-line-chart": { MiniLineChart: () => null },
    "@/components/factor-research/research-report": { ResearchReport: () => null },
    "@/components/factor-research/stock-factor-editor": { StockFactorEditor: () => null },
    "@/components/factor-research/research-report-actions": { ResearchReportActions: () => null },
    "@/components/factor-research/research-restore-panel": { ResearchRestorePanel: () => null },
    "@/components/factor-research/research-job-panel": { ResearchJobPanel: () => null },
    "@/components/factor-research/research-coverage-panel": { ResearchCoveragePanel: () => null },
    "@/components/factor-research/stock-research-input-check": { StockResearchInputCheck: () => null },
    "@/components/factor-research/research-preprocess-controls": { ResearchPreprocessControls: wrapper },
    "@/lib/stock-research-coverage": { getStockResearchCoverage: async (query, token, signal) => {
      calls.coverage.push({ query, token, signal })
      if (controls.coverageFails) throw new Error("覆盖连接失败")
      return controls.deferredCoverage ?? { ...controls.coverage, requested: query.start_date ? { start_date: query.start_date, end_date: query.end_date || "2026-10-18" } : controls.coverage.recommended }
    } },
    "@/lib/stock-research-api": researchApi,
    "@/components/factor-research/use-research-task": {
      useResearchTask: () => ({ availability: { enabled, accepting, saved_report_artifacts: artifacts, selective_coverage: selective }, busy: controls.busy, blocked: controls.blocked, recoveryPending: controls.recoveryPending, job: controls.job, result: controls.result, error: controls.analysisFails && calls.analysis.length ? "持仓缺价，不能估值" : "", submit: async (kind, parameters) => {
        if (kind === "analysis") calls.analysis.push({ factor: parameters.factor_id, body: parameters })
        else calls.backtest.push(parameters)
      } }),
    },
    "@/stores/auth": { useAuthStore: (selector) => selector({ user: { id: "user" }, accessToken: "token" }) },
    "@/lib/utils": { cn: (...values) => values.filter(Boolean).join(" ") },
    "@/lib/stock-api": {
      getStockFactorsApi: async () => {
        calls.catalog += 1
        if (controls.catalogFails) throw new Error("因子目录连接失败")
        return catalog ?? [{ id: "ret_20d", label: "20日收益率", group: "动量", direction: "high" }]
      },
      getStockDataStatusApi: async () => {
        calls.status += 1
        if (controls.statusFails) throw new Error("数据状态连接失败")
        return { instruments: 5558, kline_rows: 54229, valuation_rows: 54229, financial_rows: 100, latest_trade_date: "2026-09-30" }
      },
      analyzeStockFactorApi: async (factor, body) => {
        calls.analysis.push({ factor, body: JSON.parse(JSON.stringify(body)) })
        if (controls.analysisFails) throw new Error("持仓缺价，不能估值")
        return { factor_id: factor, decay: [], quantile_returns: [], long_short_nav: [] }
      },
      runStockBacktestApi: async (body) => {
        calls.backtest.push(JSON.parse(JSON.stringify(body)))
        return { nav: [], trades: [], rebalance_count: 0, params: {} }
      },
    },
  }
  const module = { exports: {} }
  vm.runInNewContext(output, {
    module, exports: module.exports, Error, AbortController,
    setTimeout: (callback, delay) => { const id = ++timerId; calls.delays.push(delay); timers.set(id, callback); return id }, clearTimeout: (id) => timers.delete(id),
    require: (name) => dependencies[name] ?? requireModule(name),
  })
  const render = () => {
    cursor = 0
    const tree = module.exports.FactorResearchWorkspace({ token: "token", scope: "user:virtual", initialTab, title, view })
    effects.splice(0).forEach((effect) => effect())
    return tree
  }
  render()
  const flush = async () => { for (let index = 0; index < 4; index++) { render(); await settle(); const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach((callback) => callback()); await settle() } }
  return { render, controls, calls, ready: flush, checkCoverage: flush, unmount() { slots.forEach((slot) => slot?.cleanup?.()) } }
}

function elements(tree) {
  if (Array.isArray(tree)) return tree.flatMap(elements)
  if (!tree || typeof tree !== "object") return []
  return [tree, ...elements(tree.props?.children)]
}

function textContent(tree) {
  if (Array.isArray(tree)) return tree.map(textContent).join("")
  if (tree == null || typeof tree === "boolean") return ""
  return typeof tree === "object" ? textContent(tree.props?.children) : String(tree)
}

function find(tree, predicate) {
  const element = elements(tree).find(predicate)
  assert.ok(element, "required page control is rendered")
  return element
}

const settle = () => new Promise((resolve) => setImmediate(resolve))
const button = (tree, label) => find(tree, (element) => element.type === "button" && textContent(element).includes(label))

test("custom factor authoring is lazy, uses current scope and never starts research", async () => {
  const page = mountResearch(); await page.ready()
  const editor = (tree) => elements(tree).find((element) => element.type?.name === "StockFactorEditor" && element.props?.token === "token" && element.props?.scope === "user:virtual")
  assert.equal(editor(page.render()), undefined)
  button(page.render(), "自定义因子定义管理").props.onClick()
  assert.ok(editor(page.render()))
  assert.equal(page.calls.analysis.length, 0)
  assert.equal(page.calls.backtest.length, 0)
  page.unmount()
})

test("analysis to backtest is an explicit draft transfer and never submits a task", async () => {
  const page = mountResearch()
  await page.ready()
  setPeriod(page, "2026-07-13", "2026-09-11")
  const preprocessing = { winsorize: "none", standardize: false, fillna: false, industry_neutral: false, mktcap_neutral: false }
  find(page.render(), (element) => element.props?.value?.winsorize === "mad" && typeof element.props.onChange === "function").props.onChange(preprocessing)
  await page.checkCoverage()
  const transfer = button(page.render(), "用当前因子配置回测")
  assert.ok(transfer)
  transfer.props.onClick()
  await page.ready()
  assert.ok(elements(page.render()).some((element) => element.props?.value === "backtest" && typeof element.props.onValueChange === "function"))
  assert.equal(page.calls.analysis.length, 0)
  assert.equal(page.calls.backtest.length, 0)
  button(page.render(), "开始回测").props.onClick()
  await settle()
  assert.equal(page.calls.backtest.length, 1)
  assert.deepEqual(Array.from(page.calls.backtest[0].factor_ids), ["ret_20d"])
  assert.equal(page.calls.backtest[0].start_date, "2026-07-13")
  assert.equal(page.calls.backtest[0].end_date, "2026-09-11")
  assert.deepEqual(page.calls.backtest[0].preprocess, preprocessing)
  page.unmount()
})

test("editable preprocessing and ten groups flow into the actual analysis and backtest submissions", async () => {
  const page = mountResearch(); await page.ready()
  const value = { winsorize: "none", standardize: false, fillna: false, industry_neutral: true, mktcap_neutral: true }
  const controls = find(page.render(), (element) => element.props?.value?.winsorize === "mad" && typeof element.props.onChange === "function")
  controls.props.onChange(value)
  find(page.render(), (element) => element.props?.["aria-label"] === "分组数量").props.onChange({ target: { value: "10" } })
  await page.checkCoverage()
  button(page.render(), "开始检验").props.onClick(); button(page.render(), "开始回测").props.onClick(); await settle()
  assert.equal(page.calls.analysis[0].body.groups, 10)
  assert.deepEqual(page.calls.analysis[0].body.preprocess, value)
  assert.deepEqual(page.calls.backtest[0].preprocess, value)
})

test("dedicated stock backtest entry starts on backtest and links real persisted reports", async () => {
  const page = mountResearch({ initialTab: "backtest", title: "A股历史回测" })
  await page.ready()
  assert.ok(elements(page.render()).some((element) => element.props?.value === "backtest" && typeof element.props.onValueChange === "function"))
  assert.match(textContent(page.render()), /A股历史回测/)
  assert.ok(elements(page.render()).some((element) => element.props?.href === "/history"))
})

test("restored analysis cannot silently replace the dedicated backtest entry tab", async () => {
  const page = mountResearch({ initialTab: "backtest", job: { id: "11111111-1111-4111-8111-111111111111", kind: "analysis", params: { factor_id: "ret_20d", start_date: "2026-07-13", end_date: "2026-09-11" } } })
  await page.ready()
  assert.ok(elements(page.render()).some((element) => element.props?.value === "backtest" && typeof element.props.onValueChange === "function"))
  assert.equal(page.calls.analysis.length + page.calls.backtest.length, 0)
})

function setPeriod(page, start, end) {
  const tree = page.render()
  find(tree, (element) => element.props?.id === "research-start-date").props.onChange({ target: { value: start } })
  find(tree, (element) => element.props?.id === "research-end-date").props.onChange({ target: { value: end } })
}

test("one accessible date pair sends the selected period to analysis and backtest", async () => {
  const page = mountResearch()
  await page.ready()
  const dates = elements(page.render()).filter((element) => element.type === "input" && element.props.type === "date")
  assert.equal(dates.length, 2)
  for (const input of dates) {
    assert.ok(elements(page.render()).some((element) => element.type === "label" && element.props.htmlFor === input.props.id))
  }
  setPeriod(page, "2026-07-13", "2026-09-11")
  await page.checkCoverage()
  button(page.render(), "开始检验").props.onClick()
  button(page.render(), "开始回测").props.onClick()
  await settle()
  assert.equal(page.calls.analysis[0].body.start_date, "2026-07-13")
  assert.equal(page.calls.analysis[0].body.end_date, "2026-09-11")
  assert.equal(page.calls.backtest[0].start_date, "2026-07-13")
  assert.equal(page.calls.backtest[0].end_date, "2026-09-11")
})

test("blank end date is preserved for the backend today default", async () => {
  const page = mountResearch()
  await page.ready()
  setPeriod(page, "2026-01-01", "")
  await page.checkCoverage()
  button(page.render(), "开始检验").props.onClick()
  button(page.render(), "开始回测").props.onClick()
  await settle()
  assert.equal(page.calls.analysis[0].body.end_date, "")
  assert.equal(page.calls.backtest[0].end_date, "")
})

test("inverted research dates show a visible error and never issue analysis or backtest", async () => {
  const page = mountResearch()
  await page.ready()
  setPeriod(page, "2026-09-11", "2026-07-13")
  button(page.render(), "开始检验").props.onClick()
  button(page.render(), "开始回测").props.onClick()
  await settle()
  assert.equal(page.calls.analysis.length, 0)
  assert.equal(page.calls.backtest.length, 0)
  assert.match(textContent(page.render()), /开始日期不得晚于结束日期/)
  assert.ok(elements(page.render()).some((element) => element.props?.role === "alert"))
})

test("catalog and status failures are visible and each can recover through retry", async () => {
  const page = mountResearch({ catalogFails: true, statusFails: true })
  await page.ready()
  assert.match(textContent(page.render()), /因子目录连接失败/)
  assert.match(textContent(page.render()), /数据状态连接失败/)
  assert.equal(button(page.render(), "开始检验").props.disabled, true)
  page.controls.catalogFails = false
  page.controls.statusFails = false
  button(page.render(), "重试因子目录").props.onClick()
  button(page.render(), "重试数据状态").props.onClick()
  await settle()
  assert.equal(page.calls.catalog, 2)
  assert.equal(page.calls.status, 2)
  assert.doesNotMatch(textContent(page.render()), /连接失败/)
  assert.match(textContent(page.render()), /5558/)
  assert.equal(button(page.render(), "开始检验").props.disabled, false)
})

test("business data conflicts remain visible for the chosen broad period", async () => {
  const page = mountResearch({ analysisFails: true })
  await page.ready()
  setPeriod(page, "2026-01-01", "2026-09-30")
  await page.checkCoverage()
  button(page.render(), "开始检验").props.onClick()
  await settle()
  assert.match(textContent(page.render()), /持仓缺价，不能估值/)
  assert.equal(page.calls.analysis[0].body.start_date, "2026-01-01")
  assert.equal(page.calls.analysis[0].body.end_date, "2026-09-30")
})

test("research uses durable jobs and shares report rendering without synchronous fallback", () => {
  assert.match(source, /useResearchTask/)
  assert.match(source, /ResearchReport/)
  assert.doesNotMatch(source, /analyzeStockFactorApi|runStockBacktestApi/)
  assert.match(source, /accepting/)
})

test("both analysis and backtest stay disabled when jobs are off or worker is unavailable", async () => {
  for (const options of [{ enabled: false }, { accepting: false }]) {
    const page = mountResearch(options); await page.ready()
    assert.equal(button(page.render(), "开始检验").props.disabled, true)
    assert.equal(button(page.render(), "开始回测").props.disabled, true)
  }
})

test("initial dates use the real recommendation and coverage remains readable with jobs off", async () => {
  const page = mountResearch({ enabled: false })
  assert.equal(find(page.render(), (element) => element.props?.id === "research-start-date").props.value, "")
  await page.ready()
  assert.equal(find(page.render(), (element) => element.props?.id === "research-start-date").props.value, "2026-07-01")
  assert.equal(find(page.render(), (element) => element.props?.id === "research-end-date").props.value, "2026-09-30")
  assert.ok(page.calls.coverage.length)
  assert.equal(page.calls.coverage[0].token, "token")
})

test("unverified edits, no data, and confirmed limits block submit while precheck failure offers recovery", async () => {
  const page = mountResearch(); await page.ready()
  setPeriod(page, "2026-06-01", "2026-09-30")
  assert.equal(button(page.render(), "开始检验").props.disabled, true)
  button(page.render(), "开始检验").props.onClick()
  assert.equal(page.calls.analysis.length, 0)
  for (const status of ["no_data", "over_limit"]) {
    page.controls.coverage = { ...coverage, status }
    setPeriod(page, status === "no_data" ? "2026-05-01" : "2026-04-01", "2026-09-30")
    await page.checkCoverage()
    assert.equal(button(page.render(), "开始检验").props.disabled, true)
    assert.equal(button(page.render(), "开始回测").props.disabled, true)
  }
  page.controls.coverageFails = true
  setPeriod(page, "2026-03-01", "2026-09-30"); await page.checkCoverage()
  const panel = find(page.render(), (element) => element.props?.error === "覆盖连接失败")
  assert.equal(panel.props.coverage, null)
  page.controls.coverageFails = false; page.controls.coverage = coverage
  panel.props.onRefresh(); await page.checkCoverage()
  assert.equal(button(page.render(), "开始检验").props.disabled, false)
})

test("restoration recovers backtest parameters once and late recommendation cannot overwrite them or later edits", async () => {
  let finish
  const deferredCoverage = new Promise((resolve) => { finish = resolve })
  const page = mountResearch({ deferredCoverage }); await page.ready()
  const params = { start_date: "20260713", end_date: "2026/09/11", factor_ids: ["ret_20d"], top_n: 7, rebalance: "week", weight: "factor", commission_rate: 0.002, slippage_rate: 0.003, preprocess: { winsorize: "none", standardize: false, fillna: false, industry_neutral: true, mktcap_neutral: true } }
  page.controls.job = { id: "11111111-1111-4111-8111-111111111111", kind: "backtest", params }
  await page.checkCoverage()
  page.controls.deferredCoverage = null; finish({ ...coverage, requested: coverage.recommended }); await page.checkCoverage()
  assert.equal(find(page.render(), (element) => element.props?.id === "research-start-date").props.value, "2026-07-13")
  assert.ok(elements(page.render()).some((element) => element.props?.value === "backtest" && typeof element.props.onValueChange === "function"))
  button(page.render(), "开始回测").props.onClick(); await settle()
  assert.deepEqual(JSON.parse(JSON.stringify(page.calls.backtest[0])), { ...params, start_date: "2026-07-13", end_date: "2026-09-11" })
  setPeriod(page, "2026-08-01", "2026-09-11")
  page.controls.job = { ...page.controls.job, status: "completed" }
  await page.checkCoverage()
  assert.equal(find(page.render(), (element) => element.props?.id === "research-start-date").props.value, "2026-08-01")
})

test("initial restored analysis wins before recommendation and retains horizon, groups and preprocess", async () => {
  const params = { start_date: "2026-07-13", end_date: "2026-09-11", factor_id: "ret_20d", horizon: 10, groups: 3, preprocess: { winsorize: "3sigma", standardize: false, fillna: true, industry_neutral: false, mktcap_neutral: false } }
  const page = mountResearch({ job: { id: "11111111-1111-4111-8111-111111111111", kind: "analysis", params } }); await page.ready()
  assert.equal(find(page.render(), (element) => element.props?.id === "research-start-date").props.value, params.start_date)
  button(page.render(), "开始检验").props.onClick(); await settle()
  assert.deepEqual(JSON.parse(JSON.stringify(page.calls.analysis[0].body)), params)
})

test("a superseded coverage request is aborted and cannot overwrite new parameters", async () => {
  const page = mountResearch(); await page.ready()
  let finish
  page.controls.deferredCoverage = new Promise((resolve) => { finish = resolve })
  setPeriod(page, "2026-06-01", "2026-09-30"); await page.checkCoverage()
  const stale = page.calls.coverage.at(-1)
  page.controls.deferredCoverage = null
  setPeriod(page, "2026-07-13", "2026-09-11"); await page.checkCoverage()
  assert.equal(stale.signal.aborted, true)
  finish({ ...coverage, status: "over_limit", requested: { start_date: "2026-06-01", end_date: "2026-09-30" } }); await settle()
  assert.equal(button(page.render(), "开始检验").props.disabled, false)
  page.unmount()
  assert.equal(page.calls.coverage.at(-1).signal.aborted, true)
})

test("manual editing before the initial recommendation resolves survives its late response", async () => {
  let finish
  const page = mountResearch({ deferredCoverage: new Promise((resolve) => { finish = resolve }) }); await page.ready()
  const original = page.calls.coverage[0]
  page.controls.deferredCoverage = null
  setPeriod(page, "2026-07-13", "2026-09-11")
  const before = page.calls.coverage.length
  page.render()
  assert.equal(page.calls.coverage.length, before)
  await page.checkCoverage()
  assert.equal(original.signal.aborted, true)
  assert.ok(page.calls.delays.includes(350))
  finish({ ...coverage, requested: coverage.recommended }); await settle()
  assert.equal(find(page.render(), (element) => element.props?.id === "research-start-date").props.value, "2026-07-13")
})

test("date changes clear a saved old curve while the completed task and coverage refresh remain visible", async () => {
  const page = mountResearch({ job: { id: "11111111-1111-4111-8111-111111111111", kind: "backtest", params: { start_date: "2026-07-13", end_date: "2026-09-11", factor_ids: ["ret_20d"] } } })
  page.controls.result = { nav: [{ date: "2026-09-11", nav: 1.1 }] }
  await page.ready()
  assert.ok(elements(page.render()).some((element) => element.props?.result === page.controls.result))
  setPeriod(page, "2026-08-01", "2026-09-11"); await page.checkCoverage()
  assert.equal(elements(page.render()).some((element) => element.props?.result === page.controls.result), false)
  const route = readFileSync(new URL("../../app/(main)/factor-research/page.tsx", import.meta.url), "utf8")
  const entry = readFileSync(new URL("./stock-research-entry.tsx", import.meta.url), "utf8")
  assert.match(route, /<StockResearchEntry/)
  assert.match(entry, /key=\{scope \+ token \+/)
})

const restorePanel = (page) => find(page.render(), (element) => typeof element.props?.onApply === "function" && Array.isArray(element.props.factors))

test("explicit history analysis fill restores every parameter, refreshes coverage and requires a separate submission", async () => {
  const page = mountResearch(); await page.ready()
  const preprocess = { winsorize: "3sigma", standardize: false, fillna: false, industry_neutral: true, mktcap_neutral: true }
  restorePanel(page).props.onApply({ kind: "analysis", parameters: { start_date: "20260713", end_date: "20260911", factor_id: "ret_20d", horizon: 7, groups: 10, preprocess } })
  assert.equal(page.calls.analysis.length + page.calls.backtest.length, 0)
  assert.equal(button(page.render(), "开始检验").props.disabled, true)
  await page.checkCoverage()
  assert.deepEqual(JSON.parse(JSON.stringify(page.calls.coverage.at(-1).query)), { start_date: "2026-07-13", end_date: "2026-09-11", factor_ids: ["ret_20d"], mktcap_neutral: true, weight: "equal" })
  button(page.render(), "开始检验").props.onClick(); await settle()
  assert.deepEqual(JSON.parse(JSON.stringify(page.calls.analysis[0].body)), { start_date: "2026-07-13", end_date: "2026-09-11", factor_id: "ret_20d", horizon: 7, groups: 10, preprocess })
})

test("factor dependencies invalidate coverage immediately and reject stale responses before submission", async () => {
  const page = mountResearch()
  await page.ready()
  let finish
  page.controls.deferredCoverage = new Promise((resolve) => { finish = resolve })
  const old = page.calls.coverage.at(-1)
  const control = find(page.render(), (element) => element.props?.value?.winsorize === "mad" && typeof element.props.onChange === "function")
  control.props.onChange({ ...control.props.value, mktcap_neutral: true })
  assert.equal(button(page.render(), "开始检验").props.disabled, true)
  button(page.render(), "开始检验").props.onClick()
  await page.checkCoverage()
  assert.equal(old.signal.aborted, true)
  assert.equal(page.calls.analysis.length, 0)
  assert.equal(page.calls.coverage.at(-1).query.mktcap_neutral, true)
  page.controls.deferredCoverage = null
  finish({ ...coverage })
  await page.checkCoverage()
  button(page.render(), "开始检验").props.onClick(); await settle()
  assert.equal(page.calls.analysis.length, 1)
})

test("an older running backend retains conservative legacy preflight until selective capability is advertised", async () => {
  const page = mountResearch({ selective: false })
  await page.ready()
  assert.equal(page.calls.coverage.at(-1).query.factor_ids, undefined)
  assert.equal(page.calls.coverage.at(-1).query.mktcap_neutral, undefined)
})

test("explicit history backtest fill preserves weekly frequency, weights, holdings and proportional costs", async () => {
  const page = mountResearch(); await page.ready()
  const parameters = { start_date: "2026-07-13", end_date: "", factor_ids: ["ret_20d"], rebalance: "week", top_n: 37, weight: "factor", commission_rate: 0.002, slippage_rate: 0.003, preprocess: { winsorize: "none", standardize: true, fillna: false, industry_neutral: true, mktcap_neutral: false } }
  restorePanel(page).props.onApply({ kind: "backtest", parameters })
  assert.equal(page.calls.backtest.length, 0)
  await page.checkCoverage()
  button(page.render(), "开始回测").props.onClick(); await settle()
  assert.deepEqual(JSON.parse(JSON.stringify(page.calls.backtest[0])), parameters)
})

test("history apply cannot bypass running and uncertain-submission guards, including stale handlers", async () => {
  for (const condition of ["busy", "blocked", "active"]) {
    const page = mountResearch(); await page.ready()
    const previous = find(page.render(), (element) => element.props?.id === "research-start-date").props.value
    const staleApply = restorePanel(page).props.onApply
    if (condition === "active") page.controls.job = { id: "11111111-1111-4111-8111-111111111111", status: "running", kind: "analysis" }
    else page.controls[condition] = true
    assert.equal(restorePanel(page).props.blocked, true)
    staleApply({ kind: "analysis", parameters: { start_date: "2026-08-01", end_date: "2026-09-11", factor_id: "ret_20d" } })
    assert.equal(find(page.render(), (element) => element.props?.id === "research-start-date").props.value, previous)
    assert.equal(page.calls.analysis.length + page.calls.backtest.length, 0)
  }
})

test("same-job poll and late recovered saved configuration cannot overwrite an explicitly restored draft", async () => {
  const saved = { id: "11111111-1111-4111-8111-111111111111", status: "completed", kind: "analysis", params: { factor_id: "ret_20d", start_date: "2026-07-13", end_date: "2026-09-11" } }
  const page = mountResearch({ job: saved }); await page.ready()
  setPeriod(page, "2026-08-01", "2026-09-11")
  page.controls.job = { ...saved, params: { ...saved.params } }; await page.checkCoverage()
  assert.equal(find(page.render(), (element) => element.props?.id === "research-start-date").props.value, "2026-08-01")
  restorePanel(page).props.onApply({ kind: "analysis", parameters: { ...saved.params, start_date: "2026-08-15" } })
  page.controls.job = { ...saved, id: "22222222-2222-4222-8222-222222222222" }; await page.checkCoverage()
  assert.equal(find(page.render(), (element) => element.props?.id === "research-start-date").props.value, "2026-08-15")
})

test("workspace exposes authorized downloads beside actual completed saved reports", async () => {
  const page = mountResearch({ job: { id: "11111111-1111-4111-8111-111111111111", kind: "backtest", status: "completed", params: { start_date: "2026-07-13", end_date: "2026-09-11", factor_ids: ["ret_20d"] } } })
  page.controls.result = { nav: [] }; await page.ready()
  assert.ok(elements(page.render()).some((element) => element.props?.jobId === page.controls.job.id && element.props.token === "token" && element.props.scope === "user:virtual"))
})

test("workspace does not expose export until backend report capability is deployed", async () => {
  const page = mountResearch({ artifacts: false, job: { id: "11111111-1111-4111-8111-111111111111", kind: "backtest", status: "completed", params: { start_date: "2026-07-13", end_date: "2026-09-11", factor_ids: ["ret_20d"] } } })
  page.controls.result = { nav: [] }; await page.ready()
  assert.equal(elements(page.render()).some((element) => element.props?.jobId), false)
})

test("pending recovery visibly disables creation as well as historical parameter replacement", async () => {
  const page = mountResearch(); await page.ready()
  page.controls.recoveryPending = true
  assert.equal(button(page.render(), "开始检验").props.disabled, true)
  assert.equal(button(page.render(), "开始回测").props.disabled, true)
  assert.equal(restorePanel(page).props.blocked, true)
})

const costInput = (page, name) => find(page.render(), (element) => element.type === "input" && element.props?.["aria-label"] === name)
const factorChoice = (page, id) => find(page.render(), (element) => element.type === "button" && element.props?.["data-stock-factor-id"] === id)

test("backtest sidebar selects actual portfolio factors, preserving the separate analysis factor", async () => {
  const page = mountResearch({ initialTab: "backtest", catalog: [{ id: "ret_20d", label: "20日收益率", group: "动量", direction: "high" }, { id: "pb", label: "市净率", group: "价值", direction: "low" }] })
  await page.ready()
  assert.equal(factorChoice(page, "ret_20d").props["aria-pressed"], true)
  factorChoice(page, "pb").props.onClick()
  assert.equal(factorChoice(page, "pb").props["aria-pressed"], true)
  assert.equal(button(page.render(), "开始回测").props.disabled, true)
  await page.checkCoverage()
  button(page.render(), "开始回测").props.onClick(); await settle()
  assert.deepEqual(Array.from(page.calls.backtest[0].factor_ids), ["ret_20d", "pb"])
  assert.deepEqual(Array.from(page.calls.coverage.at(-1).query.factor_ids), ["ret_20d", "pb"])
  const tabs = find(page.render(), (element) => typeof element.props?.onValueChange === "function")
  tabs.props.onValueChange("analysis"); await page.checkCoverage()
  assert.equal(factorChoice(page, "ret_20d").props["aria-pressed"], true)
  assert.equal(factorChoice(page, "pb").props["aria-pressed"], false)
  button(page.render(), "开始检验").props.onClick(); await settle()
  assert.equal(page.calls.analysis[0].factor, "ret_20d")
})

test("basis point controls preserve API proportional costs and block empty or out of range entries", async () => {
  const page = mountResearch({ initialTab: "backtest" }); await page.ready()
  assert.equal(costInput(page, "手续费（基点）").props.value, "3")
  assert.equal(costInput(page, "滑点（基点）").props.value, "10")
  costInput(page, "手续费（基点）").props.onChange({ target: { value: "2.5" } })
  costInput(page, "滑点（基点）").props.onChange({ target: { value: "8" } })
  button(page.render(), "开始回测").props.onClick(); await settle()
  assert.equal(page.calls.backtest[0].commission_rate, 0.00025)
  assert.equal(page.calls.backtest[0].slippage_rate, 0.0008)
  assert.match(textContent(page.render()), /1基点 = 0\.01%/)
  for (const invalid of ["", "-1", "101", "NaN"]) {
    costInput(page, "手续费（基点）").props.onChange({ target: { value: invalid } })
    assert.equal(button(page.render(), "开始回测").props.disabled, true)
    button(page.render(), "开始回测").props.onClick(); await settle()
    assert.equal(page.calls.backtest.length, 1)
  }
  costInput(page, "手续费（基点）").props.onChange({ target: { value: "0" } })
  button(page.render(), "开始回测").props.onClick(); await settle()
  assert.equal(page.calls.backtest[1].commission_rate, 0)
})

test("saved current-task parameters explicitly refill the draft and costs without submitting", async () => {
  const params = { start_date: "2026-07-13", end_date: "2026-09-11", factor_ids: ["ret_20d"], rebalance: "week", top_n: 37, weight: "factor", commission_rate: 0.00025, slippage_rate: 0.0008 }
  const page = mountResearch({ initialTab: "backtest", job: { id: "11111111-1111-4111-8111-111111111111", kind: "backtest", status: "completed", params } }); await page.ready()
  assert.equal(costInput(page, "手续费（基点）").props.value, "2.5")
  costInput(page, "手续费（基点）").props.onChange({ target: { value: "99" } })
  setPeriod(page, "2026-08-01", "2026-09-11"); await page.checkCoverage()
  button(page.render(), "恢复当前任务参数").props.onClick()
  assert.equal(page.calls.backtest.length, 0)
  assert.equal(button(page.render(), "开始回测").props.disabled, true)
  assert.equal(costInput(page, "手续费（基点）").props.value, "2.5")
  await page.checkCoverage()
  button(page.render(), "开始回测").props.onClick(); await settle()
  assert.equal(page.calls.backtest[0].start_date, params.start_date)
  assert.equal(page.calls.backtest[0].top_n, 37)
  assert.equal(page.calls.backtest[0].weight, "factor")
  assert.equal(page.calls.backtest[0].commission_rate, params.commission_rate)
  assert.match(textContent(page.render()), /当前回测草稿.*37只.*每周.*因子秩加权/)
})

test("sidebar and saved-task restore handlers cannot mutate a draft after work becomes active", async () => {
  for (const state of ["busy", "blocked", "recoveryPending", "active"]) {
    const page = mountResearch({ initialTab: "backtest", job: { id: "11111111-1111-4111-8111-111111111111", kind: "backtest", status: "completed", params: { start_date: "2026-07-13", end_date: "2026-09-11", factor_ids: ["ret_20d"] } } }); await page.ready()
    setPeriod(page, "2026-08-01", "2026-09-11"); await page.checkCoverage()
    const staleFactor = factorChoice(page, "ret_20d").props.onClick
    const staleRestore = button(page.render(), "恢复当前任务参数").props.onClick
    if (state === "active") page.controls.job = { ...page.controls.job, status: "running" }
    else page.controls[state] = true
    page.render()
    assert.equal(factorChoice(page, "ret_20d").props.disabled, true)
    assert.equal(button(page.render(), "恢复当前任务参数").props.disabled, true)
    staleFactor(); staleRestore()
    assert.equal(factorChoice(page, "ret_20d").props["aria-pressed"], true)
    assert.equal(find(page.render(), (element) => element.props?.id === "research-start-date").props.value, "2026-08-01")
    assert.equal(page.calls.analysis.length + page.calls.backtest.length, 0)
  }
})

test("dedicated analysis and backtest expose only their own controls and expected restore kind", async () => {
  for (const view of ["analysis", "backtest"]) {
    const page = mountResearch({ view }); await page.ready()
    const tree = page.render()
    const text = textContent(tree)
    assert.match(text, view === "analysis" ? /开始检验/ : /开始回测/)
    assert.doesNotMatch(text, view === "analysis" ? /开始回测|组合策略参数|用当前因子配置回测/ : /开始检验|未来收益周期|分组数量/)
    assert.equal(restorePanel(page).props.expectedKind, view)
    assert.equal(elements(tree).some((node) => node.props?.value === (view === "analysis" ? "backtest" : "analysis") && !node.props?.onChange), false)
    assert.equal(elements(tree).some((node) => node.props?.href === "/factor-lab"), true)
    assert.equal(elements(tree).some((node) => node.type?.name === "StockFactorEditor"), false)
    page.unmount()
  }
})

test("dedicated pages keep the other kind global task without restoring dates, costs or report", async () => {
  for (const view of ["analysis", "backtest"]) {
    const other = view === "analysis" ? "backtest" : "analysis"
    const params = { start_date: "2026-03-02", end_date: "2026-04-30", ...(other === "analysis" ? { factor_id: "ret_20d", horizon: 60 } : { factor_ids: ["ret_20d"], commission_rate: 0.009 }) }
    const page = mountResearch({ view, job: { id: "11111111-1111-4111-8111-111111111111", kind: other, status: "completed", params } })
    page.controls.result = { saved: true }; await page.ready()
    const tree = page.render()
    assert.equal(find(tree, (node) => node.props?.id === "research-start-date").props.value, coverage.recommended.start_date)
    assert.equal(elements(tree).some((node) => node.type?.name === "ResearchReport"), false)
    assert.equal(elements(tree).some((node) => node.type?.name === "ResearchJobPanel" && node.props.task.job.kind === other), true)
    assert.equal(elements(tree).some((node) => node.type === "button" && textContent(node).includes("恢复当前任务参数")), false)
    assert.equal(elements(tree).some((node) => node.props?.href === (other === "analysis" ? "/factor-research" : "/backtest")), true)
    if (view === "backtest") assert.equal(costInput(page, "手续费（基点）").props.value, "3")
    assert.equal(page.calls.analysis.length + page.calls.backtest.length, 0)
    page.unmount()
  }
})

test("a dedicated page rejects wrong-kind draft application without changing the valid draft or precheck", async () => {
  for (const view of ["analysis", "backtest"]) {
    const page = mountResearch({ view }); await page.ready()
    setPeriod(page, "2026-08-03", "2026-09-11"); await page.checkCoverage()
    const before = page.calls.coverage.length
    restorePanel(page).props.onApply({ kind: view === "analysis" ? "backtest" : "analysis", parameters: { start_date: "2026-03-02", end_date: "2026-04-30", ...(view === "analysis" ? { factor_ids: ["ret_20d"] } : { factor_id: "ret_20d" }) } })
    await page.checkCoverage()
    assert.equal(find(page.render(), (node) => node.props?.id === "research-start-date").props.value, "2026-08-03")
    assert.equal(page.calls.coverage.length, before)
    assert.match(textContent(page.render()), /请在.*页面恢复/)
    assert.equal(page.calls.analysis.length + page.calls.backtest.length, 0)
    page.unmount()
  }
})

test("dedicated pages still lock their own submission while another kind task is active or uncertain", async () => {
  for (const view of ["analysis", "backtest"]) {
    const page = mountResearch({ view }); await page.ready()
    page.controls.busy = true
    page.controls.job = { id: "11111111-1111-4111-8111-111111111111", kind: view === "analysis" ? "backtest" : "analysis", status: "running" }
    assert.equal(button(page.render(), view === "analysis" ? "开始检验" : "开始回测").props.disabled, true)
    assert.equal(restorePanel(page).props.blocked, true)
    page.controls.busy = false; page.controls.job = null; page.controls.blocked = true
    assert.equal(button(page.render(), view === "analysis" ? "开始检验" : "开始回测").props.disabled, true)
    page.unmount()
  }
})
