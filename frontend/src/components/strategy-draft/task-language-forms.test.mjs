import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import vm from "node:vm"
import test from "node:test"
import ts from "typescript"
import { parseTaskLanguageDraft } from "../../lib/task-language-draft.ts"

const modelId = "11111111-1111-4111-8111-111111111111"
const context = { contracts: [{ symbol: "rb2701", name: "螺纹钢2701", exchange: "SHFE" }], models: [{ id: modelId, display_name: "用户模型", model_id: "chat", provider_api_type: "openai", capabilities: [] }], selectedModelId: modelId, today: "2026-10-18" }
const requireModule = createRequire(import.meta.url)
const root = fileURLToPath(new URL("../../", import.meta.url))
const components = new Map()
const component = (name) => { if (!components.has(name)) components.set(name, () => null); return components.get(name) }
const stub = (...names) => Object.fromEntries(names.map((name) => [name, component(name)]))

function mount(kind) {
  const slots = [], effects = [], calls = []
  let cursor = 0
  const dependencies = {
    react: {
      useState(initial) { const position = cursor++; if (!(position in slots)) slots[position] = typeof initial === "function" ? initial() : initial; return [slots[position], (value) => { slots[position] = typeof value === "function" ? value(slots[position]) : value }] },
      useEffect(effect, deps) { const position = cursor++; if (!(position in slots) || deps.some((value, index) => value !== slots[position].deps[index])) { slots[position]?.cleanup?.(); slots[position] = { deps }; effects.push(() => { slots[position].cleanup = effect() }) } },
    },
    "@/components/ui/dialog": stub("Dialog", "DialogContent", "DialogHeader", "DialogTitle"),
    "@/components/ui/button": stub("Button"), "@/components/ui/input": stub("Input"), "@/components/ui/label": stub("Label"),
    "@/components/strategy-draft/task-language-draft-panel": stub("TaskLanguageDraftPanel"),
    "@/lib/api": { getAIModels: async () => context.models },
    "@/stores/ai-trading": { useAITradingStore: (selector) => selector({ createTask: async (payload) => { calls.push(payload) } }) },
  }
  for (const [path, names] of [
    ["ai-trading/form/symbol-picker", ["SymbolPicker"]], ["ai-trading/task-icon", ["TaskIcon"]], ["ai-trading/form/icon-picker", ["IconPicker"]],
    ["ai-trading/form/ai-factor-mount", ["AiFactorMount"]], ["ai-trading/form/ai-quant-ref-picker", ["AiQuantRefPicker"]], ["ai-trading/form/estimate-capital-hint", ["EstimateCapitalHint"]],
    ["ai-trading/form/create-quant-params", ["CreateQuantParams"]], ["backtest/backtest-form-fields", ["StrategySection"]], ["backtest/backtest-range-slider", ["BacktestRangeSlider"]],
  ]) dependencies[`@/components/${path}`] = stub(...names)
  const cache = new Map()
  function load(path) {
    if (cache.has(path)) return cache.get(path)
    const module = { exports: {} }
    cache.set(path, module.exports)
    const output = ts.transpileModule(readFileSync(path, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
    const localRequire = (name) => {
      if (dependencies[name]) return dependencies[name]
      if (name.startsWith("@/") || name.startsWith(".")) {
        const stem = name.startsWith("@/") ? resolve(root, name.slice(2)) : resolve(dirname(path), name)
        const candidate = [stem, `${stem}.ts`, `${stem}.tsx`].find(existsSync)
        assert.ok(candidate, name)
        return load(candidate)
      }
      return requireModule(name)
    }
    vm.runInNewContext(output, { module, exports: module.exports, require: localRequire, Date, Number, Error, Set, Map })
    return module.exports
  }
  const path = kind === "ai_task" ? "components/ai-trading/form/create-task-dialog.tsx" : kind === "quant_task" ? "components/ai-trading/form/create-quant-dialog.tsx" : "components/backtest/backtest-form.tsx"
  const module = load(resolve(root, path))
  const render = () => {
    cursor = 0
    const tree = kind === "ai_task" ? module.CreateTaskDialog({ open: true, onClose() {} }) : kind === "quant_task" ? module.CreateQuantDialog({ open: true, onClose() {} }) : module.BacktestForm({ submitting: false, onSubmit: (payload) => calls.push(payload) })
    effects.splice(0).forEach((effect) => effect())
    return tree
  }
  render()
  return { render, calls }
}
function elements(tree) { if (Array.isArray(tree)) return tree.flatMap(elements); return tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : [] }
function text(tree) { if (Array.isArray(tree)) return tree.map(text).join(""); return tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }
function find(page, predicate) { const value = elements(page.render()).find(predicate); assert.ok(value, "expected form control"); return value }
const settle = () => new Promise((resolve) => setImmediate(resolve))
const payload = (kind, description) => { const result = parseTaskLanguageDraft(description, kind, context); assert.deepEqual(result.clarifications, []); return result.draft }

test("quant draft confirmation only prefills; manual creation preserves exact parameters and disables automatic start", async () => {
  const page = mount("quant_task")
  const draft = payload("quant_task", "rb2701，15分钟，双均线，快线7，慢线30，只做多，2手，资金二十万元，止损3%，止盈8%")
  find(page, (element) => element.type === component("TaskLanguageDraftPanel")).props.onApply(draft)
  assert.equal(page.calls.length, 0)
  find(page, (element) => element.type === component("Button") && text(element) === "创建").props.onClick(); await settle()
  assert.equal(page.calls.length, 1)
  const created = page.calls[0]
  assert.equal(created.auto_start, false)
  assert.equal(created.symbol, "rb2701")
  assert.equal(created.fixed_qty, 2)
  assert.equal(created.allocated_capital, 200000)
  assert.equal(created.stop_rules.loss_pct, 3)
  assert.equal(created.close_rules.pnl_pct, 8)
  assert.deepEqual(JSON.parse(JSON.stringify(created.strategy_params)), { fast_period: 7, slow_period: 30, ma_type: "sma" })
})

test("AI draft uses the owned model and original instruction without making any request until manual create", async () => {
  const page = mount("ai_task"); await settle()
  const draft = payload("ai_task", "rb2701，5分钟，AI交易，双向，3手，资金十五万元，持仓最多3天；策略说明：趋势不明确就观望")
  find(page, (element) => element.type === component("TaskLanguageDraftPanel")).props.onApply(draft)
  assert.equal(page.calls.length, 0)
  await settle()
  find(page, (element) => element.type === component("Button") && text(element) === "创建").props.onClick(); await settle()
  const created = page.calls[0]
  assert.ok(created)
  assert.equal(created.auto_start, false)
  assert.equal(created.model_row_id, modelId)
  assert.equal(created.custom_prompt, "趋势不明确就观望")
  assert.equal(created.custom_prompt_enabled, true)
  assert.equal(created.qty_min, 3)
  assert.equal(created.qty_max, 3)
  assert.equal(created.max_hold_days, 3)
})

test("backtest draft atomically restores explicit dates and requires the original run button", async () => {
  const page = mount("backtest")
  const draft = payload("backtest", "rb2701，日线，N日突破，回看30根，双向，2手，本金十万元，2026年7月1日至2026年9月30日")
  find(page, (element) => element.type === component("TaskLanguageDraftPanel")).props.onApply(draft)
  assert.equal(page.calls.length, 0)
  const range = find(page, (element) => element.type === component("BacktestRangeSlider"))
  assert.equal(range.props.start, "2026-07-01")
  assert.equal(range.props.end, "2026-09-30")
  find(page, (element) => element.type === component("Button") && text(element) === "开始回测").props.onClick(); await settle()
  const created = page.calls[0]
  assert.equal(created.strategy_type, "n_breakout")
  assert.equal(created.initial_cash, 100000)
  assert.equal(created.fixed_qty, 2)
  assert.equal(created.multi_segment, false)
  assert.deepEqual(JSON.parse(JSON.stringify(created.strategy_params)), { lookback: 30 })
})

test("opening a language panel does not change the existing manual quant creation defaults", async () => {
  const page = mount("quant_task")
  find(page, (element) => element.type === component("SymbolPicker")).props.onChange("rb2701", "螺纹钢2701")
  find(page, (element) => element.type === component("Button") && text(element) === "创建").props.onClick(); await settle()
  assert.equal(page.calls[0].strategy_type, "n_breakout")
  assert.equal(page.calls[0].auto_start, true)
})
