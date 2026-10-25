import { z } from "zod"
import { validateBacktestRange } from "../components/backtest/timeframe-limits.ts"

const integer = z.number().int().min(1).max(500)
const strategy = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("ai"), params: z.object({}).strict() }).strict(),
  z.object({ kind: z.literal("ma_cross"), params: z.object({ fast_period: integer, slow_period: integer.min(2), ma_type: z.literal("sma") }).strict().refine((value) => value.fast_period < value.slow_period, "均线快线必须小于慢线") }).strict(),
  z.object({ kind: z.literal("n_breakout"), params: z.object({ lookback: integer.min(2) }).strict() }).strict(),
  z.object({ kind: z.literal("macd_cross"), params: z.object({ fast_period: integer.min(2).max(100), slow_period: integer.min(3).max(200), signal_period: integer.min(2).max(100) }).strict().refine((value) => value.fast_period < value.slow_period, "MACD快线必须小于慢线") }).strict(),
  z.object({ kind: z.literal("kdj_cross"), params: z.object({ n_period: integer.min(2).max(100), k_period: integer.min(2).max(50), d_period: integer.min(2).max(50), use_zone: z.literal(false) }).strict() }).strict(),
  z.object({ kind: z.literal("band_swing"), params: z.object({ period: integer.min(5).max(200), std_mult: z.number().min(0.5).max(5) }).strict() }).strict(),
])

export const taskLanguageDraftSchema = z.object({
  parser_version: z.literal("task-language-rules-v1"),
  target: z.enum(["ai_task", "quant_task", "backtest"]),
  source_text: z.string().min(1).max(2000),
  name: z.string().max(120),
  symbol: z.string().regex(/^[a-z]{1,3}\d{3,4}$/),
  symbol_name: z.string().max(100),
  timeframe: z.enum(["1m", "5m", "15m", "30m", "60m", "1d"]),
  side_mode: z.enum(["long_only", "short_only", "both"]),
  fixed_qty: z.number().int().min(1).max(10000),
  capital: z.number().min(1000).max(100000000).refine((value) => Math.abs(Math.round(value * 100) - value * 100) < 0.00001, "资金精度最多到分"),
  strategy,
  model_row_id: z.uuid().nullable(),
  model_label: z.string().nullable(),
  start_date: z.iso.date().nullable(),
  end_date: z.iso.date().nullable(),
  risk_style: z.enum(["aggressive", "balanced", "conservative"]),
  max_hold_days: z.number().int().min(1).max(365).nullable(),
  loss_pct: z.number().positive().max(100).nullable(),
  profit_pct: z.number().positive().max(100).nullable(),
  loss_amount: z.number().positive().max(100000000).nullable(),
  instructions: z.string().min(1).max(1200).nullable(),
  auto_start: z.literal(false),
}).strict().superRefine((value, context) => {
  const reject = (message: string) => context.addIssue({ code: "custom", message })
  if (value.target === "ai_task" && value.strategy.kind !== "ai") reject("AI任务只支持AI配置，规则策略请使用量化任务")
  if (value.target === "quant_task" && value.strategy.kind === "ai") reject("量化任务不支持AI配置")
  if (value.strategy.kind === "ai" && !value.model_row_id) reject("请选择当前用户的对话模型")
  if (value.strategy.kind !== "ai" && (value.model_row_id || value.instructions)) reject("规则策略不能包含模型或自由文本指令")
  if (value.target === "backtest") {
    if (!value.start_date || !value.end_date) reject("请提供回测起止日期")
    else {
      const error = validateBacktestRange({ startDate: value.start_date, endDate: value.end_date, timeframe: value.timeframe })
      if (error) reject(error)
    }
    if (value.capital < 10000) reject("回测本金至少10000元")
    if (value.fixed_qty > 100) reject("回测手数上限100手")
    if (value.loss_amount !== null) reject("回测表单暂不支持金额止损，请改用百分比")
    if (value.max_hold_days !== null) reject("回测表单暂不支持自定义最长持仓天数")
  } else if (value.start_date || value.end_date) reject("任务创建不支持按历史日期运行，请改用历史回测")
})

export type TaskLanguageDraft = z.infer<typeof taskLanguageDraftSchema>
export type TaskLanguageTarget = TaskLanguageDraft["target"]

const contractSchema = z.object({ symbol: z.string().min(1).max(20), name: z.string().max(100), exchange: z.string().max(50) })
const modelSchema = z.object({ id: z.uuid(), display_name: z.string(), model_id: z.string(), provider_api_type: z.string(), capabilities: z.array(z.string()) })
export type TaskLanguageContext = { contracts: z.infer<typeof contractSchema>[]; models: z.infer<typeof modelSchema>[]; selectedModelId?: string; today?: string }

export async function loadTaskLanguageContext(token: string, includeModels: boolean, signal?: AbortSignal): Promise<TaskLanguageContext> {
  const timeout = AbortSignal.timeout(20000)
  const bounded = signal ? AbortSignal.any([signal, timeout]) : timeout
  const request = async (path: string) => {
    const response = await fetch(`${(process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "")}${path}`, { method: "GET", cache: "no-store", headers: { Authorization: `Bearer ${token}` }, signal: bounded })
    if (!response.ok) {
      const body = await response.json().catch(() => null)
      throw new Error(typeof body?.detail === "string" ? body.detail : `目录读取失败: ${response.status}`)
    }
    return response.json()
  }
  const contracts = z.array(contractSchema).max(20000).parse(await request("/api/market/contracts"))
  const models = includeModels ? z.array(modelSchema).max(5000).parse(await request("/api/ai/models")) : []
  return { contracts, models: models.filter((model) => model.provider_api_type !== "jev" && !model.capabilities.includes("decision")) }
}

const numberPattern = "[+-]?(?:[0-9]+(?:\\.[0-9]+)?(?:万|亿)?|[零〇一二两三四五六七八九十百千万亿]+)"
const chineseDigits: Record<string, number> = { 零: 0, 〇: 0, 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 }

function formatChineseInteger(value: number): string {
  for (const unit of [100000000, 10000]) {
    if (value >= unit) {
      const remainder = value % unit
      return formatChineseInteger(Math.floor(value / unit)) + (unit === 10000 ? "万" : "亿") + (remainder ? `${remainder < unit / 10 ? "零" : ""}${formatChineseInteger(remainder)}` : "")
    }
  }
  let formatted = ""
  let zero = false
  for (const place of [1000, 100, 10, 1]) {
    const digit = Math.floor(value / place) % 10
    if (!digit) { if (formatted && value % place) zero = true; continue }
    if (zero) formatted += "零"
    zero = false
    if (!(place === 10 && digit === 1 && !formatted)) formatted += "零一二三四五六七八九"[digit]
    formatted += place === 1000 ? "千" : place === 100 ? "百" : place === 10 ? "十" : ""
  }
  return formatted || "零"
}

function readNumber(value: string): number {
  const arabic = /^([+-]?\d+(?:\.\d+)?)(万|亿)?$/.exec(value)
  if (arabic) return Number(arabic[1]) * (arabic[2] === "万" ? 10000 : arabic[2] === "亿" ? 100000000 : 1)
  if (/^[零〇一二两三四五六七八九]+$/.test(value)) return Number([...value].map((character) => chineseDigits[character]).join(""))
  let total = 0
  let section = 0
  let digit = 0
  for (const character of value) {
    if (character in chineseDigits) digit = chineseDigits[character]
    else if (character === "万" || character === "亿") {
      section += digit
      if (character === "亿") { total = (total + section) * 100000000; section = 0 }
      else { total += section * 10000; section = 0 }
      digit = 0
    } else if (character === "十" || character === "百" || character === "千") {
      section += (digit || 1) * (character === "十" ? 10 : character === "百" ? 100 : 1000)
      digit = 0
    } else return NaN
  }
  const parsed = total + section + digit
  const normalized = value.replace(/两/g, "二").replace(/〇/g, "零").replace(/(^|万|亿|零)一十/g, "$1十")
  return Number.isSafeInteger(parsed) && normalized === formatChineseInteger(parsed) ? parsed : NaN
}

export function parseTaskLanguageDraft(source: string, target: TaskLanguageTarget, context: TaskLanguageContext): { draft: TaskLanguageDraft | null; clarifications: string[] } {
  const clarifications: string[] = []
  if (!source.trim() || source.length > 2000) return { draft: null, clarifications: ["请填写1至2000字的配置描述"] }
  const instruction = /(?:策略说明|交易说明)[：:]([\s\S]*)$/.exec(source)
  let remaining = (instruction ? source.slice(0, instruction.index) : source).normalize("NFKC")
  const unique = <Value,>(values: Value[], label: string): Value | undefined => {
    const distinct = [...new Set(values)]
    if (distinct.length > 1) clarifications.push(`${label}出现冲突，请只保留一个明确值`)
    return distinct[0]
  }
  const strings = (pattern: RegExp, group = 1) => {
    const values: string[] = []
    remaining = remaining.replace(pattern, (...matches: string[]) => { values.push(matches[group]); return " " })
    return values
  }
  const scalar = (expression: string, label: string) => unique(strings(new RegExp(expression, "gi")).map(readNumber), label)
  const name = unique(strings(/(?:任务名称|名称)[：:]\s*([^,;\n]+)/g), "名称") ?? ""
  const requestedModel = unique(strings(/模型[：:]\s*([^,;\n]+)/gi).map((value) => value.trim()), "模型")
  remaining = remaining.replace(/(?:不要|不)(?:自动|立即)?启动|先不运行|创建后不运行|仅创建/g, " ")
  if (/立即启动|自动启动|自动开始|马上运行|立即下单|现在下单|下单|实盘|真实账户|绕过|执行代码|运行代码/i.test(remaining)) clarifications.push("语言草稿不支持启动、下单或代码执行；请仅描述配置，创建后手动启动")
  const dates = strings(/(\d{4}(?:-|\/|年)\d{1,2}(?:-|\/|月)\d{1,2}日?)/g).map((value) => {
    const digits = value.match(/\d+/g) ?? []
    return `${digits[0]}-${digits[1]?.padStart(2, "0")}-${digits[2]?.padStart(2, "0")}`
  })
  if (dates.length !== 0 && dates.length !== 2) clarifications.push("请提供唯一的一组起止日期")
  if (target === "backtest" && dates.length === 0) clarifications.push("请明确回测起止日期，例如2026年7月1日至2026年9月30日")
  const symbol = unique(strings(/\b([a-z]{1,3}\d{3,4})\b/gi).map((value) => value.toLowerCase()), "合约")
  const matchedContracts = context.contracts.filter((contract) => contract.symbol.toLowerCase() === symbol)
  const contract = matchedContracts[0]
  if (!symbol || !contract) clarifications.push("请提供当前合约目录中的完整合约代码，如rb2701；简称或主力须先选择具体合约")
  if (matchedContracts.length > 1 && new Set(matchedContracts.map((value) => value.exchange)).size > 1) clarifications.push("合约代码对应多个交易所，请在品种选择器中确认")
  const quantities = strings(new RegExp(`(?:每次|固定|数量|手数)?\\s*(${numberPattern})\\s*手`, "gi")).map(readNumber)
  const explicitQuantity = scalar(`(?:手数|数量)[：:]?\\s*(${numberPattern})`, "手数")
  if (explicitQuantity !== undefined) quantities.push(explicitQuantity)
  const fixedQty = unique(quantities, "手数")
  if (fixedQty === undefined) clarifications.push("请明确每次交易手数，如2手")
  const lossPct = scalar(`止损\\s*(${numberPattern})\\s*%`, "止损百分比") ?? null
  const profitPct = scalar(`止盈\\s*(${numberPattern})\\s*%`, "止盈百分比") ?? null
  const lossAmount = scalar(`止损\\s*(${numberPattern})\\s*元`, "止损金额") ?? null
  const amounts = strings(new RegExp(`(?:初始资金|回测本金|资金仓|资金|本金|预算)[：:]?\\s*(${numberPattern})\\s*(?:元|块)?`, "g")).map(readNumber)
  amounts.push(...strings(new RegExp(`(${numberPattern})\\s*(?:元|块)`, "g")).map(readNumber))
  const capital = unique(amounts, "资金")
  if (capital === undefined) clarifications.push("请明确资金金额（元），如资金十万元")
  const timeframes = strings(new RegExp(`(?:K线周期|周期)?\\s*(${numberPattern})\\s*(?:分钟|分|m)(?:线)?`, "gi")).map((value) => `${readNumber(value)}m`)
  timeframes.push(...strings(/(日线|1d)/gi).map(() => "1d"))
  const timeframe = unique(timeframes, "K线周期")
  if (!timeframe) clarifications.push("请明确K线周期：1/5/15/30/60分钟或日线")
  const directions = strings(/(只做多|仅做多|只多|仅多|只做空|仅做空|只空|仅空|多空都做|多空|双向)/g).map((value) => /多空|双向/.test(value) ? "both" : /多/.test(value) ? "long_only" : "short_only")
  const sideMode = unique(directions, "多空方向")
  if (!sideMode) clarifications.push("请明确方向：只做多、只做空或双向")
  const maxHoldDays = scalar(`(?:持仓最多|最长持仓|持仓上限)\\s*(${numberPattern})\\s*天`, "最长持仓天数") ?? null
  const riskNames = strings(/(稳健|均衡|保守|激进)/g)
  const riskStyle = unique(riskNames.map((value) => value === "保守" ? "conservative" : value === "激进" ? "aggressive" : "balanced"), "风险风格") ?? "balanced"
  const fast = scalar(`快线\\s*(${numberPattern})(?:根|期)?`, "快线")
  const slow = scalar(`慢线\\s*(${numberPattern})(?:根|期)?`, "慢线")
  const signal = scalar(`信号(?:线)?\\s*(${numberPattern})(?:根|期)?`, "信号线")
  const lookback = scalar(`回看\\s*(${numberPattern})(?:根|期)?`, "回看根数")
  const bandPeriod = scalar(`(?:布林周期|周期)\\s*(${numberPattern})\\s*根`, "布林周期")
  const deviation = scalar(`(?:标准差|倍标准差)\\s*(${numberPattern})`, "标准差倍数")
  const kdjN = scalar(`\\bN\\s*(${numberPattern})`, "KDJ的N")
  const kdjK = scalar(`\\bK\\s*(${numberPattern})`, "KDJ的K")
  const kdjD = scalar(`\\bD\\s*(${numberPattern})`, "KDJ的D")
  const dayLookback = scalar(`(${numberPattern})日突破`, "突破天数")
  const kinds = [
    ...strings(/(双均线|均线交叉|ma_cross)/gi).map(() => "ma_cross"),
    ...strings(/(N日突破|N根突破|通道突破|唐奇安|n_breakout)/gi).map(() => "n_breakout"),
    ...strings(/(MACD(?:金叉死叉)?|macd_cross)/gi).map(() => "macd_cross"),
    ...strings(/(KDJ(?:金叉死叉)?|kdj_cross)/gi).map(() => "kdj_cross"),
    ...strings(/(布林带(?:波段)?|band_swing)/gi).map(() => "band_swing"),
    ...strings(/(AI(?:交易|任务)?|人工智能交易)/gi).map(() => "ai"),
  ]
  if (dayLookback !== undefined) {
    kinds.push("n_breakout")
    if (timeframe !== "1d") clarifications.push("分钟周期的突破参数是K线根数，请写N日突破、回看20根；不能把20日直接换成20根")
  }
  const kind = unique(kinds, "策略") ?? (target === "ai_task" ? "ai" : undefined)
  if (!kind) clarifications.push("请明确受支持策略：双均线、N日突破、MACD、KDJ、布林带或AI")
  const need = (value: number | undefined, label: string) => { if (value === undefined) clarifications.push(`请明确${label}`); return value }
  let params: Record<string, unknown> = {}
  if (kind === "ma_cross") params = { fast_period: need(fast, "均线快线周期"), slow_period: need(slow, "均线慢线周期"), ma_type: "sma" }
  else if (kind === "macd_cross") params = { fast_period: need(fast, "MACD快线周期"), slow_period: need(slow, "MACD慢线周期"), signal_period: need(signal, "MACD信号线周期") }
  else if (kind === "n_breakout") params = { lookback: need(unique([lookback, dayLookback].filter((value) => value !== undefined), "突破周期"), "突破回看根数") }
  else if (kind === "kdj_cross") params = { n_period: need(kdjN, "KDJ的N"), k_period: need(kdjK, "KDJ的K"), d_period: need(kdjD, "KDJ的D"), use_zone: false }
  else if (kind === "band_swing") params = { period: need(bandPeriod, "布林周期根数"), std_mult: need(deviation, "布林标准差倍数") }
  const usedParameters = new Set(kind === "ma_cross" ? ["fast", "slow"] : kind === "macd_cross" ? ["fast", "slow", "signal"] : kind === "n_breakout" ? ["lookback", "dayLookback"] : kind === "kdj_cross" ? ["kdjN", "kdjK", "kdjD"] : kind === "band_swing" ? ["bandPeriod", "deviation"] : [])
  Object.entries({ fast, slow, signal, lookback, dayLookback, kdjN, kdjK, kdjD, bandPeriod, deviation }).forEach(([key, value]) => { if (value !== undefined && !usedParameters.has(key)) clarifications.push("描述中包含不适用于当前策略的参数，请删除或改选策略") })
  const ownedModels = context.models.filter((model) => model.provider_api_type !== "jev" && !model.capabilities.includes("decision"))
  const matchedModels = requestedModel ? ownedModels.filter((model) => [model.id, model.display_name, model.model_id].includes(requestedModel)) : ownedModels.filter((model) => model.id === context.selectedModelId)
  const model = kind === "ai" && matchedModels.length === 1 ? matchedModels[0] : null
  if (kind === "ai" && !model) clarifications.push("请从当前用户的模型列表选择唯一对话模型，或明确写模型：显示名称")
  if (kind !== "ai" && (requestedModel || instruction || riskNames.length)) clarifications.push("规则策略不支持模型、风险风格或策略说明，请使用AI模式或移除这些字段")
  remaining = remaining.replace(/历史回测|量化任务|交易任务|量化交易|配置草稿|请|帮我|创建|新建|一个|一份|做个|做|进行|执行|回测|模拟盘|模拟|量化|交易|任务|采用|使用|策略|配置|同时|并且|以及|并|每次|固定|从|到|至|用|的|按|以|[\s,;:。.!！?？、]/g, "")
  if (remaining) clarifications.push(`尚未支持或含糊的条件：${remaining.slice(0, 100)}。请明确配置，不会忽略条件生成其它策略。`)
  const parsed = taskLanguageDraftSchema.safeParse({ parser_version: "task-language-rules-v1", target, source_text: source, name, symbol: symbol ?? "", symbol_name: contract?.name ?? "", timeframe, side_mode: sideMode, fixed_qty: fixedQty, capital, strategy: { kind, params }, model_row_id: model?.id ?? null, model_label: model?.display_name ?? null, start_date: dates[0] ?? null, end_date: dates[1] ?? null, risk_style: riskStyle, max_hold_days: maxHoldDays, loss_pct: lossPct, profit_pct: profitPct, loss_amount: lossAmount, instructions: instruction?.[1] ?? null, auto_start: false })
  if (!parsed.success && !clarifications.length) clarifications.push(...parsed.error.issues.map((issue) => `${issue.path.join(".")}: ${issue.message}`))
  const today = context.today ?? new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date())
  if (dates.some((value) => value > today)) clarifications.push("回测结束日期不得晚于上海今天")
  return parsed.success && !clarifications.length ? { draft: parsed.data, clarifications: [] } : { draft: null, clarifications: [...new Set(clarifications)] }
}
