import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import vm from "node:vm"
import { createRequire } from "node:module"
import { jsx } from "react/jsx-runtime"
import { renderToStaticMarkup } from "react-dom/server"
import ts from "typescript"

const source = readFileSync(new URL("./research-report.tsx", import.meta.url), "utf8")
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
const charts = []
const module = { exports: {} }, requireModule = createRequire(import.meta.url)
vm.runInNewContext(output, { module, exports: module.exports, require: (name) => name === "@/components/factor-research/mini-line-chart" ? { MiniLineChart: (props) => { charts.push(props); return jsx("div", { "data-chart": true }) } } : name === "@/lib/utils" ? { cn: (...values) => values.join(" ") } : requireModule(name) })
const metadata = { research_job_id: "11111111-1111-4111-8111-111111111111", data_version: "version-fingerprint", data_manifest: { source: "本地授权导入库", rows: 100 }, method_version: "method-v1", workflow_version: "jobs-v1", validation_status: "research_only", validation_limits: ["历史修订待验证"] }

test("report explains saved neutralization identifiability and never backfills older methodology", () => {
  const job = { id: metadata.research_job_id, kind: "analysis" }
  const result = { ...metadata, neutralization_policy: { version: "neutralization-identifiability-v1", min_industry_observations: 2, min_residual_degrees_of_freedom: 1, requires_full_column_rank: true, insufficient_sample: "exclude_without_imputation" } }
  const html = renderToStaticMarkup(module.exports.ResearchReport({ job, result }))
  for (const phrase of ["neutralization-identifiability-v1", "行业组至少2", "残差自由度至少1", "满列秩", "不足保持缺失"]) assert.ok(html.includes(phrase), phrase)
  const older = renderToStaticMarkup(module.exports.ResearchReport({ job, result: metadata }))
  assert.ok(older.includes("中性化政策：未记录"))
  assert.ok(!older.includes("neutralization-identifiability-v1"))
})

test("shared factor report renders original two charts, null IC and precise research risks", () => {
  charts.length = 0
  const result = { ...metadata, ic: { ic_mean: 0.02, ic_ir: null, rank_ic_mean: 0.03, ic_positive_ratio: 0.6 }, decay: [{ lag: 5, rank_ic: null }, { lag: 10, rank_ic: 0.03 }], long_short_nav: [{ date: "2026-09-11", nav: 1.01 }], quantile_returns: [{ group: 0, avg_return: 0.02 }] }
  const html = renderToStaticMarkup(module.exports.ResearchReport({ job: { id: metadata.research_job_id, kind: "analysis", workflow_version: "jobs-v1", params: { horizon: 10 } }, result }))
  assert.equal(charts.length, 2)
  assert.equal(charts[0].points.length, 1)
  assert.equal(charts[1].points[0].y, 1.01)
  for (const phrase of ["未来 10 日", "零成本", "未来收益有重叠", "不可变原始数据归档", "version-fingerprint", "待验证", "2.00%", "—"]) assert.ok(html.includes(phrase), phrase)
})

test("shared backtest report retains daily NAV, real cost wording and bounded latest trade details", () => {
  charts.length = 0
  const result = { ...metadata, metrics: { total_return_pct: 1, annualized_return_pct: 2, annualized_vol_pct: 3, sharpe: 0.1, calmar: null, max_drawdown_pct: 0, win_rate: 50, turnover_rate: 0.2 }, nav: [{ date: "2026-09-11", nav: 1.01 }], rebalance_count: 2, trades: Array.from({ length: 65 }, (_, index) => ({ date: "2026-09-11", code: `600${index}`, name: `trade-${index}`, weight: 0.05, action: "buy", factor_value: null })) }
  const html = renderToStaticMarkup(module.exports.ResearchReport({ job: { id: metadata.research_job_id, kind: "backtest", workflow_version: "jobs-v1" }, result }))
  assert.equal(charts.length, 1)
  assert.equal(charts[0].points[0].y, 1.01)
  for (const phrase of ["60", "持仓份额保持至下次调仓", "实际买卖金额", "trade-64", "5.0%", "—"]) assert.ok(html.includes(phrase), phrase)
  assert.ok(!html.includes("trade-0<"))
})

test("report uses saved grouping and preprocessing instead of hardcoded five groups", () => {
  const result = { ...metadata, ic: { ic_mean: 0, ic_ir: null, rank_ic_mean: 0, ic_positive_ratio: 0 }, decay: [], long_short_nav: [], quantile_returns: [] }
  const html = renderToStaticMarkup(module.exports.ResearchReport({ job: { id: metadata.research_job_id, kind: "analysis", params: { horizon: 5, groups: 10, preprocess: { winsorize: "none", standardize: false, fillna: false, industry_neutral: true, mktcap_neutral: true } } }, result }))
  assert.ok(html.includes("分 10 组"))
  assert.ok(!html.includes("分 5 组"))
  for (const phrase of ["不去极值", "标准化关", "缺失值处理关", "行业中性开", "市值中性开"]) assert.ok(html.includes(phrase), phrase)
})

test("report shows persisted factor domain policy counts and marks older policies unrecorded", () => {
  const base = { ...metadata, ic: { ic_mean: 0, ic_ir: null, rank_ic_mean: 0, ic_positive_ratio: 0 }, decay: [], long_short_nav: [], quantile_returns: [] }
  const job = { id: metadata.research_job_id, kind: "analysis" }
  const older = renderToStaticMarkup(module.exports.ResearchReport({ job, result: base }))
  assert.ok(older.includes("因子样本政策：未记录"))
  const factor_policies = [{ factor_id: "pb", policy_version: "stock-factor-domain-v1", eligibility_rule: "finite_strictly_positive", missing_value_policy: "exclude_without_imputation", exclusion_rule: "PE/PB非正、缺失或非有限值排除，预处理不得恢复", count_unit: "date_security_observations", start_date: "2024-01-01", end_date: "2024-03-15", counts: { observations: 170, raw_eligible: 120, excluded_nonpositive: 20, excluded_nonfinite: 20, missing: 10, postprocess_eligible: 120, imputed: 0 } }]
  const html = renderToStaticMarkup(module.exports.ResearchReport({ job, result: { ...base, factor_policies } }))
  for (const phrase of ["stock-factor-domain-v1", "PE/PB非正", "日期×证券", "原始合格120", "非正20", "非有限20", "缺失10", "预处理后120", "填补0", "2024-01-01"]) assert.ok(html.includes(phrase), phrase)
})

test("factor groups have a fixed zero baseline and never invent nonzero height for zero return", () => {
  const result = { ...metadata, ic: { ic_mean: 0, ic_ir: null, rank_ic_mean: 0, ic_positive_ratio: 0 }, decay: [], long_short_nav: [], quantile_returns: [{ group: 0, avg_return: -0.02 }, { group: 1, avg_return: 0 }, { group: 2, avg_return: 0.04 }] }
  const html = renderToStaticMarkup(module.exports.ResearchReport({ job: { id: metadata.research_job_id, kind: "analysis" }, result }))
  assert.ok(html.includes('aria-label="分组收益零轴"'))
  assert.ok(html.includes('data-return-sign="negative"'))
  assert.ok(html.includes('data-return-sign="zero"'))
  assert.ok(html.includes('data-return-sign="positive"'))
  assert.ok(html.includes('-2.00%'))
  assert.ok(!html.includes('min-height:4px'))
})

test("known v5 report explains daily win ratio and full trade truncation without changing saved numbers", () => {
  const result = { ...metadata, method_version: "stock-research-2026-10-19.v5", metrics: { total_return_pct: 1, annualized_return_pct: 2, annualized_vol_pct: 3, sharpe: 0.1, calmar: null, max_drawdown_pct: 0, win_rate: 50, turnover_rate: 0.2 }, nav: [], rebalance_count: 2, trades: [], trades_total: 800, trades_truncated: true }
  const html = renderToStaticMarkup(module.exports.ResearchReport({ job: { id: metadata.research_job_id, kind: "backtest" }, result }))
  for (const phrase of ["正收益日占比", "平均调仓换手比例", "244", "无风险收益默认0", "800", "已截断", "50.0%", "0.200"]) assert.ok(html.includes(phrase), phrase)
  const older = renderToStaticMarkup(module.exports.ResearchReport({ job: { id: metadata.research_job_id, kind: "backtest" }, result: { ...result, method_version: "unrecorded", trades_truncated: false } }))
  assert.ok(older.includes("统计口径：未识别"))
  assert.ok(!older.includes("无风险收益默认0"))
})
