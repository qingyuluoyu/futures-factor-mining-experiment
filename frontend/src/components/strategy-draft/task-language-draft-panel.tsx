"use client"

import { useEffect, useRef, useState } from "react"
import { useAuthStore } from "@/stores/auth"
import { loadTaskLanguageContext, parseTaskLanguageDraft, taskLanguageDraftSchema, type TaskLanguageContext, type TaskLanguageDraft, type TaskLanguageTarget } from "@/lib/task-language-draft"

const strategyLabels = { ai: "AI交易", ma_cross: "SMA双均线", n_breakout: "N根突破", macd_cross: "MACD", kdj_cross: "KDJ", band_swing: "布林带波段" }

export function TaskLanguageDraftPanel({ target, onApply, selectedModelId, disabled = false, active = true }: {
  target: TaskLanguageTarget; onApply: (draft: TaskLanguageDraft) => void; selectedModelId?: string; disabled?: boolean; active?: boolean
}): React.JSX.Element {
  const token = useAuthStore((state) => state.accessToken)
  const userId = useAuthStore((state) => state.user?.id)
  const [text, setText] = useState("")
  const [draft, setDraft] = useState<TaskLanguageDraft | null>(null)
  const [clarifications, setClarifications] = useState<string[]>([])
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)
  const [applied, setApplied] = useState(false)
  const [catalog, setCatalog] = useState<TaskLanguageContext | null>(null)
  const [modelId, setModelId] = useState("")
  const controller = useRef<AbortController | null>(null)
  const revision = useRef(0)
  const mounted = useRef(false)
  const session = useRef({ token, userId, active, target })
  const draftSession = useRef<{ token: string; userId: string; revision: number } | null>(null)
  const sessionMatches = session.current.token === token && session.current.userId === userId && session.current.target === target && session.current.active === active && active
  const current = () => {
    const auth = useAuthStore.getState()
    return mounted.current && active && auth.accessToken === token && auth.user?.id === userId
  }

  useEffect(() => {
    mounted.current = true
    session.current = { token, userId, active, target }
    revision.current++
    setText("")
    setDraft(null)
    setClarifications([])
    setError("")
    setLoading(false)
    setApplied(false)
    setCatalog(null)
    setModelId("")
    draftSession.current = null
    return () => { mounted.current = false; controller.current?.abort() }
  }, [token, userId, active, target])

  const invalidate = () => {
    revision.current++
    controller.current?.abort()
    setDraft(null)
    setClarifications([])
    setError("")
    setLoading(false)
    setApplied(false)
    draftSession.current = null
  }

  const generate = async () => {
    if (disabled || loading || !sessionMatches || !current() || !token || !userId) return
    controller.current?.abort()
    const request = new AbortController()
    controller.current = request
    const inputRevision = revision.current
    const accepts = () => current() && controller.current === request && !request.signal.aborted && inputRevision === revision.current
    setDraft(null)
    setClarifications([])
    setError("")
    setApplied(false)
    if (!text.trim() || text.length > 2000) { setError("请填写1至2000字的配置描述"); return }
    setLoading(true)
    try {
      const context = await loadTaskLanguageContext(token, target === "ai_task" || /\bAI\b|人工智能|模型/i.test(text), request.signal)
      if (!accepts()) return
      setCatalog(context)
      const result = parseTaskLanguageDraft(text, target, { ...context, selectedModelId: modelId || selectedModelId })
      setDraft(result.draft)
      setClarifications(result.clarifications)
      if (result.draft) draftSession.current = { token, userId, revision: inputRevision }
    } catch (failure) {
      if (accepts()) setError(failure instanceof Error ? failure.message : "配置目录读取失败，请手动重试")
    } finally {
      if (accepts()) setLoading(false)
    }
  }

  const apply = () => {
    if (disabled || applied || !draft || !current() || draftSession.current?.token !== token || draftSession.current?.userId !== userId || draftSession.current?.revision !== revision.current) return
    const validated = taskLanguageDraftSchema.safeParse(draft)
    if (!validated.success || validated.data.target !== target) { setError("草稿校验失败，请重新解析"); return }
    onApply(validated.data)
    setApplied(true)
  }

  const example = target === "ai_task"
    ? "rb2701，5分钟，AI交易，双向，1手，资金十万元，稳健；策略说明：只在趋势清晰时考虑交易。"
    : `rb2701，日线，双均线，快线5，慢线20，只做多，2手，资金十万元${target === "backtest" ? "，2026年7月1日至2026年9月30日" : ""}`

  return (
    <details className="rounded-md border border-[var(--border)] p-2.5 text-xs">
      <summary className="cursor-pointer font-medium text-[var(--text-primary)]">用语言填写配置（规则解析）</summary>
      <div className="mt-2 space-y-2">
        <p className="text-[var(--text-muted)]">不调用大模型。解析为配置草稿，确认填入后仍须核对原表单并点击创建{target === "backtest" ? "或开始回测" : "；创建后不会自动启动"}。</p>
        <label className="block space-y-1">
          <span>配置描述（最多2000字）</span>
          <textarea rows={3} maxLength={2000} value={sessionMatches ? text : ""} disabled={disabled || !sessionMatches} onChange={(event) => { invalidate(); setText(event.target.value) }} placeholder={example} className="w-full rounded border border-[var(--border)] bg-[var(--bg-primary)] px-2 py-1.5 text-xs" />
        </label>
        <p className="break-all text-[11px] text-[var(--text-muted)]">示例：{example} 合约须在当前目录；AI模型须从自己的列表选择。</p>
        <p className="text-[11px] text-[var(--text-muted)]">支持SMA双均线、N根突破、MACD、KDJ、布林带及AI配置；套利、网格、组合条件、EMA及模糊品种需补充或改用已有表单。策略说明仅作为AI策略数据保存，不在解析时执行。</p>
        {sessionMatches && catalog && catalog.models.length > 0 && <label className="block space-y-1">
          <span>当前用户的对话模型</span>
          <select value={modelId || selectedModelId || ""} disabled={disabled || loading} onChange={(event) => { invalidate(); setModelId(event.target.value) }} className="block w-full rounded border border-[var(--border)] bg-[var(--bg-primary)] px-2 py-1.5">
            <option value="">请选择模型后重新解析</option>
            {catalog.models.map((model) => <option key={model.id} value={model.id}>{model.display_name} ({model.model_id})</option>)}
          </select>
        </label>}
        <button type="button" disabled={disabled || loading || !token || !userId || !active} onClick={() => void generate()} className="rounded border border-[var(--border)] px-2 py-1 disabled:opacity-50">{loading ? "读取目录并校验…" : "解析配置草稿"}</button>
        {sessionMatches && error && <p role="alert" className="text-[var(--accent-danger)]">{error}。未填入表单，请修正或手动重试。</p>}
        {sessionMatches && clarifications.length > 0 && <div role="alert" className="space-y-1 text-[var(--accent-danger)]"><p>待补充或澄清：</p>{clarifications.map((message, index) => <p key={index}>{message}</p>)}</div>}
        {sessionMatches && draft && <div className="space-y-1 rounded border border-[var(--border)] p-2">
          <p>{strategyLabels[draft.strategy.kind]} · {draft.symbol_name} ({draft.symbol}) · {draft.timeframe} · {draft.side_mode === "long_only" ? "只做多" : draft.side_mode === "short_only" ? "只做空" : "双向"}</p>
          <p>资金 {draft.capital} 元 · 每次 {draft.fixed_qty} 手{draft.start_date ? ` · ${draft.start_date} 至 ${draft.end_date}` : ""}</p>
          {Object.entries(draft.strategy.params).length > 0 && <p className="break-all">策略参数：{Object.entries(draft.strategy.params).map(([key, value]) => `${key}=${value}`).join("，")}（周期参数均为K线根数）</p>}
          <p>止损 {draft.loss_pct === null ? "未设置" : `${draft.loss_pct}%`} · 金额止损 {draft.loss_amount === null ? "未设置" : `${draft.loss_amount}元`} · 止盈 {draft.profit_pct === null ? "未设置" : `${draft.profit_pct}%`}</p>
          {draft.strategy.kind === "ai" && <p>模型：{draft.model_label} · 风格：{draft.risk_style === "conservative" ? "保守" : draft.risk_style === "aggressive" ? "激进" : "稳健"}。风格可能调整止盈止损的实际阈值，仍按现有引擎执行。</p>}
          {draft.instructions && <><p className="whitespace-pre-wrap break-words">AI策略说明（原文）：{draft.instructions}</p><p>启用策略说明后，现有引擎将停用风险风格，按用户提示词模式执行；止盈止损仍受原系统规则约束。</p></>}
          <p>最长持仓：{draft.max_hold_days === null ? "沿用该周期默认" : `${draft.max_hold_days}天`}。未指定的扩展信号、共振及运行选项将恢复默认；AI自主平仓/止损沿用现有默认。请在原表单确认。</p>
          <p className="text-[var(--text-muted)]">{target === "backtest" ? "仅填入单段回测参数，不保证所选日期有行情；实际回测可能访问原数据源，AI回测会产生模型调用成本。" : "只填入待创建配置；自动启动关闭。AI任务确认创建可能划拨模拟资金，后续手动启动可能产生模型成本。"}</p>
          <button type="button" disabled={disabled || applied} onClick={apply} className="rounded bg-[var(--primary)] px-2 py-1 text-white disabled:opacity-50">确认填入表单</button>
          {applied && <p role="status">已填入原表单，尚未创建或启动，请核对后使用原创建按钮。</p>}
        </div>}
      </div>
    </details>
  )
}
