"use client"



import { Suspense, useCallback, useEffect, useRef, useState } from "react"
import Link from "next/link"
import { FlaskConical, LineChart as LineChartIcon, Loader2 } from "lucide-react"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { ResearchReport } from "@/components/factor-research/research-report"
import { ResearchReportActions } from "@/components/factor-research/research-report-actions"
import { ResearchRestorePanel, type ResearchRestoreDraft } from "@/components/factor-research/research-restore-panel"
import { ResearchJobPanel } from "@/components/factor-research/research-job-panel"
import { ResearchCoveragePanel } from "@/components/factor-research/research-coverage-panel"
import { StockResearchInputCheck } from "@/components/factor-research/stock-research-input-check"
import { ResearchPreprocessControls } from "@/components/factor-research/research-preprocess-controls"
import { StockFactorEditor } from "@/components/factor-research/stock-factor-editor"
import { useResearchTask } from "@/components/factor-research/use-research-task"
import { isActiveResearchJob, researchCreateSchema, researchPeriodError, type ResearchKind, type ResearchResult, type ResearchPreprocess } from "@/lib/stock-research-api"
import { getStockResearchCoverage, type ResearchCoverage } from "@/lib/stock-research-coverage"
import {
  getStockFactorsApi,
  getStockDataStatusApi,
  type BacktestResult,
  type FactorAnalysisResult,
  type StockFactorSpec,
} from "@/lib/stock-api"
import { cn } from "@/lib/utils"

const GROUP_ORDER = ["价值", "成长", "质量", "动量", "波动", "流动性"]
const basisPoints = (rate: number): string => String(Number((rate * 10000).toPrecision(15)))

export function FactorResearchWorkspace({ token, scope, initialTab, view, title = view === "backtest" ? "A股历史回测" : "A股因子研究" }: { token: string; scope: string; initialTab?: ResearchKind; view?: ResearchKind; title?: string }): React.JSX.Element {
  const [factorEditorOpen, setFactorEditorOpen] = useState(false)
  const task = useResearchTask(token, scope)
  const [factors, setFactors] = useState<StockFactorSpec[]>([])
  const [dataStatus, setDataStatus] = useState<string>("")
  const [catalogLoading, setCatalogLoading] = useState(true)
  const [catalogError, setCatalogError] = useState("")
  const [statusLoading, setStatusLoading] = useState(true)
  const [statusError, setStatusError] = useState("")
  const [startDate, setStartDate] = useState("")
  const [endDate, setEndDate] = useState("")
  const [legacyTab, setActiveTab] = useState<string>(initialTab ?? "analysis")
  const activeTab = view ?? legacyTab
  const currentView = useRef(view)
  currentView.current = view
  const [groups, setGroups] = useState(5)
  const [preprocess, setPreprocess] = useState<ResearchPreprocess>({ winsorize: "mad", standardize: true, fillna: true, industry_neutral: false, mktcap_neutral: false })
  const [restoreError, setRestoreError] = useState("")
  const restoredJob = useRef<string | null>(null)
  const historyDraftApplied = useRef(false)
  const currentTask = useRef(task)
  currentTask.current = task
  const dateRevision = useRef(0)
  const coverageController = useRef<AbortController | null>(null)
  const [coverageState, setCoverageState] = useState<{ key: string; data: ResearchCoverage | null; loading: boolean; error: string }>({ key: "", data: null, loading: true, error: "" })
  const [coverageRefresh, setCoverageRefresh] = useState(0)

  // 因子检验状态
  const [selected, setSelected] = useState("ret_20d")
  const [horizon, setHorizon] = useState(20)
  const [analysis, setAnalysis] = useState<FactorAnalysisResult | null>(null)
  const [analysisError, setAnalysisError] = useState("")
  const analyzing = task.busy

  // 组合回测状态
  const [btFactors, setBtFactors] = useState<string[]>(["ret_20d"])
  const [rebalance, setRebalance] = useState("month")
  const [topN, setTopN] = useState(20)
  const [weight, setWeight] = useState("equal")
  const [commission, setCommission] = useState(0.0003)
  const [slippage, setSlippage] = useState(0.001)
  const [commissionBps, setCommissionBps] = useState("3")
  const [slippageBps, setSlippageBps] = useState("10")
  const [backtest, setBacktest] = useState<BacktestResult | null>(null)
  const [backtestError, setBacktestError] = useState("")
  const running = task.busy
  const draftLocked = task.busy || task.blocked || task.recoveryPending || isActiveResearchJob(task.job)
  const costError = [commissionBps, slippageBps].some((value) => value.trim() === "" || !Number.isFinite(Number(value)) || Number(value) < 0 || Number(value) > 100) ? "手续费和滑点须为0至100基点（0至1%），不可留空。" : ""

  const loadCatalog = useCallback(async () => {
    setCatalogLoading(true)
    setCatalogError("")
    try {
      const rows = await getStockFactorsApi()
      setFactors(rows)
      if (!rows.length) throw new Error("暂无可用因子，请重试或检查数据配置")
      setSelected((previous) => restoredJob.current || rows.some((factor) => factor.id === previous) ? previous : rows[0].id)
      setBtFactors((previous) => {
        if (restoredJob.current) return previous
        const retained = previous.filter((factorId) => rows.some((factor) => factor.id === factorId))
        return retained.length ? retained : [rows[0].id]
      })
    } catch (error) {
      setCatalogError(error instanceof Error ? error.message : "因子目录加载失败")
    } finally {
      setCatalogLoading(false)
    }
  }, [])

  const loadDataStatus = useCallback(async () => {
    setStatusLoading(true)
    setStatusError("")
    try {
      const status = await getStockDataStatusApi()
      setDataStatus(
        `股票池 ${status.instruments} 只 · 日线 ${status.kline_rows} 根 · 估值 ${status.valuation_rows} 行 · 财务 ${status.financial_rows} 期` +
          (status.latest_trade_date ? ` · 最新 ${status.latest_trade_date}` : ""),
      )
    } catch (error) {
      setStatusError(error instanceof Error ? error.message : "数据状态加载失败")
    } finally {
      setStatusLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadCatalog()
    void loadDataStatus()
  }, [loadCatalog, loadDataStatus])

  const periodError = researchPeriodError(startDate, endDate)
  const requiredFactorIds = activeTab === "backtest" ? btFactors : [selected]
  const coverageWeight = activeTab === "backtest" ? weight as "equal" | "mktcap" | "factor" : "equal"
  const selectiveCoverage = task.availability?.selective_coverage === true
  const coverageKey = JSON.stringify([token, scope, startDate, endDate, activeTab, [...requiredFactorIds].sort(), preprocess.mktcap_neutral, coverageWeight, selectiveCoverage])
  const currentCoverage = coverageState.key === coverageKey ? coverageState.data : null
  const coverageLoading = coverageState.key !== coverageKey || coverageState.loading
  const coverageError = coverageState.key === coverageKey ? coverageState.error : ""
  const coverageBlocked = coverageLoading || Boolean(coverageError) || currentCoverage?.status !== "within_limits"

  useEffect(() => {
    if (!task.job || !task.job.params || (view && task.job.kind !== view) || historyDraftApplied.current || restoredJob.current === task.job.id) return
    restoredJob.current = task.job.id
    dateRevision.current++
    try {
      const restored = researchCreateSchema.parse({ idempotency_key: task.job.id, kind: task.job.kind, parameters: task.job.params })
      const normalize = (value: string) => value ? `${value.replace(/[-/]/g, "").slice(0, 4)}-${value.replace(/[-/]/g, "").slice(4, 6)}-${value.replace(/[-/]/g, "").slice(6, 8)}` : ""
      setStartDate(normalize(restored.parameters.start_date))
      setEndDate(normalize(restored.parameters.end_date))
      setPreprocess(restored.parameters.preprocess)
      if (!view && !initialTab) setActiveTab(restored.kind)
      if (restored.kind === "analysis") {
        setSelected(restored.parameters.factor_id)
        setHorizon(restored.parameters.horizon)
        setGroups(restored.parameters.groups)
      } else {
        setBtFactors(restored.parameters.factor_ids)
        setRebalance(restored.parameters.rebalance)
        setTopN(restored.parameters.top_n)
        setWeight(restored.parameters.weight)
        setCommission(restored.parameters.commission_rate)
        setSlippage(restored.parameters.slippage_rate)
        setCommissionBps(basisPoints(restored.parameters.commission_rate))
        setSlippageBps(basisPoints(restored.parameters.slippage_rate))
      }
      setRestoreError("")
    } catch {
      setRestoreError("保存任务的参数无法恢复，请查看历史报告并手动核对参数。")
    }
  }, [task.job, initialTab, view])

  const applyHistoricalDraft = (draft: ResearchRestoreDraft) => {
    const latestTask = currentTask.current
    if (latestTask.busy || latestTask.blocked || latestTask.recoveryPending || isActiveResearchJob(latestTask.job) || catalogLoading || catalogError) return
    if (currentView.current && draft.kind !== currentView.current) {
      setRestoreError(`请在${draft.kind === "analysis" ? "因子检验" : "组合回测"}页面恢复此任务参数。`)
      return
    }
    try {
      const restored = researchCreateSchema.parse({ idempotency_key: "00000000-0000-4000-8000-000000000000", ...draft })
      const ids = restored.kind === "analysis" ? [restored.parameters.factor_id] : restored.parameters.factor_ids
      if (ids.some((id) => !factors.some((factor) => factor.id === id))) throw new Error("历史因子已失效，不能应用到当前表单。")
      const normalize = (value: string) => value ? `${value.replace(/[-/]/g, "").slice(0, 4)}-${value.replace(/[-/]/g, "").slice(4, 6)}-${value.replace(/[-/]/g, "").slice(6, 8)}` : ""
      historyDraftApplied.current = true
      dateRevision.current++
      coverageController.current?.abort()
      setStartDate(normalize(restored.parameters.start_date))
      setEndDate(normalize(restored.parameters.end_date))
      setPreprocess(restored.parameters.preprocess)
      setActiveTab(restored.kind)
      if (restored.kind === "analysis") {
        setSelected(restored.parameters.factor_id)
        setHorizon(restored.parameters.horizon)
        setGroups(restored.parameters.groups)
      } else {
        setBtFactors(restored.parameters.factor_ids)
        setRebalance(restored.parameters.rebalance)
        setTopN(restored.parameters.top_n)
        setWeight(restored.parameters.weight)
        setCommission(restored.parameters.commission_rate)
        setSlippage(restored.parameters.slippage_rate)
        setCommissionBps(basisPoints(restored.parameters.commission_rate))
        setSlippageBps(basisPoints(restored.parameters.slippage_rate))
      }
      setAnalysis(null)
      setBacktest(null)
      setAnalysisError("")
      setBacktestError("")
      setRestoreError("")
      setCoverageState({ key: "", data: null, loading: true, error: "" })
      setCoverageRefresh((value) => value + 1)
    } catch (error) {
      setRestoreError(error instanceof Error ? error.message : "保存研究参数无法应用。")
    }
  }

  const restoreCurrentTaskParameters = () => {
    const saved = currentTask.current.job
    if (saved?.params) applyHistoricalDraft({ kind: saved.kind, parameters: saved.params } as ResearchRestoreDraft)
  }

  useEffect(() => {
    const controller = new AbortController()
    coverageController.current = controller
    setCoverageState({ key: coverageKey, data: null, loading: true, error: "" })
    const revision = dateRevision.current
    const load = async () => {
      try {
        const data = await getStockResearchCoverage({ ...(startDate ? { start_date: startDate, end_date: endDate } : {}), ...(selectiveCoverage ? { factor_ids: requiredFactorIds, mktcap_neutral: preprocess.mktcap_neutral, weight: coverageWeight } : {}) }, token, controller.signal)
        if (coverageController.current !== controller || controller.signal.aborted) return
        setCoverageState({ key: coverageKey, data, loading: false, error: "" })
        if (!startDate && !endDate && revision === 0 && dateRevision.current === 0 && !restoredJob.current && data.recommended) {
          dateRevision.current++
          setStartDate(data.recommended.start_date)
          setEndDate(data.recommended.end_date)
        }
      } catch (error) {
        if (coverageController.current !== controller || controller.signal.aborted) return
        setCoverageState({ key: coverageKey, data: null, loading: false, error: error instanceof Error ? error.message : "覆盖预检失败" })
      }
    }
    const canLoad = (!startDate && !endDate) || !periodError
    const timer = canLoad ? setTimeout(() => void load(), startDate ? 350 : 0) : null
    if (!canLoad) setCoverageState({ key: coverageKey, data: null, loading: false, error: periodError })
    return () => { if (timer !== null) clearTimeout(timer); controller.abort() }
  }, [token, scope, startDate, endDate, coverageKey, periodError, coverageRefresh])

  const editDates = (start: string | undefined, end: string | undefined) => {
    if ((start === undefined || start === startDate) && (end === undefined || end === endDate)) return
    dateRevision.current++
    coverageController.current?.abort()
    if (start !== undefined) setStartDate(start)
    if (end !== undefined) setEndDate(end)
    setAnalysis(null)
    setBacktest(null)
    setAnalysisError("")
    setBacktestError("")
  }

  useEffect(() => {
    if (!task.result || !task.job || (view && task.job.kind !== view)) return
    if (task.job.kind === "analysis") setAnalysis(task.result as unknown as FactorAnalysisResult)
    else setBacktest(task.result as unknown as BacktestResult)
  }, [task.result, task.job, view])

  const runAnalysis = useCallback(async () => {
    setAnalysis(null)
    setAnalysisError(periodError)
    if ((currentView.current && currentView.current !== "analysis") || periodError || coverageBlocked) return
    await task.submit("analysis", { factor_id: selected, horizon, groups, preprocess, start_date: startDate, end_date: endDate })
  }, [selected, horizon, groups, preprocess, startDate, endDate, periodError, coverageBlocked, task])

  const runBacktest = useCallback(async () => {
    setBacktest(null)
    setBacktestError(periodError || costError)
    if ((currentView.current && currentView.current !== "backtest") || periodError || costError || coverageBlocked) return
    await task.submit("backtest", { factor_ids: btFactors, start_date: startDate, end_date: endDate, rebalance, top_n: topN, weight, commission_rate: commission, slippage_rate: slippage, preprocess })
  }, [btFactors, rebalance, topN, weight, commission, slippage, preprocess, startDate, endDate, periodError, costError, coverageBlocked, task])

  const transferToBacktest = () => {
    if (task.busy || task.blocked || task.recoveryPending || !factors.some((factor) => factor.id === selected)) return
    setBtFactors([selected])
    setBacktestError("")
    setActiveTab("backtest")
  }

  const grouped = GROUP_ORDER.map((group) => ({
    group,
    items: factors.filter((f) => f.group === group),
  })).filter((g) => g.items.length > 0)

  const toggleBtFactor = (id: string): void => {
    const latest = currentTask.current
    if (latest.busy || latest.blocked || latest.recoveryPending || isActiveResearchJob(latest.job)) return
    setBtFactors((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    )
  }

  return (
    <div className="flex h-[calc(100vh-56px)]">
      
      <aside className="w-64 shrink-0 overflow-y-auto border-r border-[var(--border)] bg-[var(--bg-secondary)] p-3">
        <h2 className="mb-1 text-sm font-semibold text-[var(--text-primary)]">
          因子目录
        </h2>
        <p className="mb-3 text-[11px] leading-relaxed text-[var(--text-muted)]">
          {view === "analysis" ? "单击选择检验因子。" : view === "backtest" ? "多选因子合成组合信号（等权平均）。" : "检验tab单击选择；回测tab可多选合成（等权平均）。"}
        </p>
        {catalogLoading && <p role="status" className="mb-3 text-xs text-[var(--text-muted)]">因子目录加载中…</p>}
        {catalogError && (
          <div role="alert" className="mb-3 text-xs text-[var(--accent-danger)]">
            <p>{catalogError}</p>
            <button type="button" onClick={() => void loadCatalog()} disabled={catalogLoading} className="mt-1 underline cursor-pointer disabled:opacity-50">重试因子目录</button>
          </div>
        )}
        {grouped.map(({ group, items }) => (
          <div key={group} className="mb-3">
            <p className="mb-1 text-[11px] font-medium text-[var(--text-muted)]">
              {group}
            </p>
            {items.map((f) => (
              <button
                key={f.id}
                type="button"
                data-stock-factor-id={f.id}
                aria-pressed={activeTab === "backtest" ? btFactors.includes(f.id) : selected === f.id}
                disabled={draftLocked}
                onClick={() => {
                  const latest = currentTask.current
                  if (latest.busy || latest.blocked || latest.recoveryPending || isActiveResearchJob(latest.job)) return
                  if (activeTab === "backtest") toggleBtFactor(f.id)
                  else setSelected(f.id)
                }}
                title={f.formula}
                className={cn(
                  "mb-1 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left cursor-pointer",
                  (activeTab === "backtest" ? btFactors.includes(f.id) : selected === f.id)
                    ? "bg-[var(--primary)]/15 text-[var(--primary)]"
                    : "hover:bg-[var(--bg-tertiary)]",
                )}
              >
                <span className="text-xs text-[var(--text-primary)]">{f.label}</span>
                <span
                  className={cn(
                    "ml-auto text-[10px]",
                    f.direction === "high"
                      ? "text-[var(--accent-up)]"
                      : "text-[var(--accent-down)]",
                  )}
                >
                  {f.direction === "high" ? "↑大好" : "↓小好"}
                </span>
              </button>
            ))}
          </div>
        ))}
        {statusError ? (
          <div role="alert" className="mt-2 text-xs text-[var(--accent-danger)]">
            <p>{statusError}</p>
            <button type="button" onClick={() => void loadDataStatus()} disabled={statusLoading} className="mt-1 underline cursor-pointer disabled:opacity-50">重试数据状态</button>
          </div>
        ) : (
          <p role="status" className="mt-2 text-[10px] leading-relaxed text-[var(--text-muted)]">
            {statusLoading ? "数据状态加载中…" : dataStatus}
          </p>
        )}
      </aside>

      
      <div className="min-w-0 flex-1 overflow-y-auto p-4">
        <header className="mb-4 space-y-2">
          <div className="flex flex-wrap items-center gap-4"><h1 className="text-lg font-semibold text-[var(--text-primary)]">{title}</h1><Link href="/history" className="text-xs underline">查看历史研究报告</Link>{view ? <Link href="/factor-lab" className="text-xs underline">因子目录与自定义定义</Link> : <button type="button" disabled={factorEditorOpen} onClick={() => setFactorEditorOpen(true)} className="text-xs underline cursor-pointer disabled:opacity-50">自定义因子定义管理</button>}</div>
          <p className="text-xs leading-6 text-[var(--text-muted)]">A股历史数据研究 · 不连接真实资金 · 先检查数据覆盖，再确认参数创建后台任务。结果保留来源、数据版本和费用假设，仅供研究，不等同可实盘策略。</p>
          {!view && initialTab && task.job && task.job.kind !== initialTab && <p role="status" className="text-xs text-[var(--text-muted)]">已恢复另一类型的最近任务，状态仍显示在下方；当前入口保留{initialTab === "backtest" ? "组合回测" : "因子检验"}页签，可切换页签或打开历史报告查看原结果。</p>}
          {view && task.job && task.job.kind !== view && <p role="status" className="text-xs text-[var(--text-muted)]">另一类型的最近任务状态保留在下方，未回填本页参数或报告。<Link href={task.job.kind === "analysis" ? "/factor-research" : "/backtest"} className="underline">进入{task.job.kind === "analysis" ? "因子检验" : "组合回测"}页面查看</Link>。</p>}
        </header>
        {!view && factorEditorOpen && <StockFactorEditor token={token} scope={scope} />}
        <ResearchJobPanel task={task} />
        {task.job?.params && (!view || task.job.kind === view) && <div className="mb-4 space-y-1 rounded border border-[var(--border)] p-3 text-xs">
          <button type="button" disabled={draftLocked || catalogLoading || Boolean(catalogError)} onClick={restoreCurrentTaskParameters} className="underline disabled:opacity-50">恢复当前任务参数</button>
          <p className="text-[var(--text-muted)]">将当前任务的已保存配置回填为草稿；重新检查数据覆盖后再手动运行。原报告和原任务保持不变。</p>
        </div>}
        <Suspense fallback={<p role="status">正在读取历史配置入口…</p>}>
          <ResearchRestorePanel token={token} scope={scope} factors={factors} catalogLoading={catalogLoading} catalogError={catalogError} blocked={task.busy || task.blocked || task.recoveryPending || isActiveResearchJob(task.job)} expectedKind={view} onApply={applyHistoricalDraft} />
        </Suspense>
        {restoreError && <p role="alert" className="mb-3 text-xs text-[var(--accent-danger)]">{restoreError}</p>}
        {task.error && <p role="alert" className="mb-3 text-xs text-[var(--accent-danger)]">{task.error}</p>}
        <fieldset disabled={analyzing || running} className="mb-4 rounded-lg border border-[var(--border)] p-3">
          <legend className="px-1 text-xs font-medium text-[var(--text-primary)]">{view === "analysis" ? "因子检验日期" : view === "backtest" ? "组合回测日期" : "研究日期（检验与回测共用）"}</legend>
          <div className="flex flex-wrap gap-3">
            <label htmlFor="research-start-date" className="text-xs text-[var(--text-secondary)]">
              研究开始日期
              <input
                id="research-start-date"
                type="date"
                required
                value={startDate}
                max={endDate || undefined}
                aria-invalid={Boolean(periodError)}
                aria-describedby="research-period-help"
                onChange={(event) => editDates(event.target.value, undefined)}
                className="mt-1 block rounded border border-[var(--border)] bg-[var(--bg-secondary)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
              />
            </label>
            <label htmlFor="research-end-date" className="text-xs text-[var(--text-secondary)]">
              研究结束日期（可留空）
              <input
                id="research-end-date"
                type="date"
                value={endDate}
                min={startDate || undefined}
                aria-invalid={Boolean(periodError)}
                aria-describedby="research-period-help"
                onChange={(event) => editDates(undefined, event.target.value)}
                className="mt-1 block rounded border border-[var(--border)] bg-[var(--bg-secondary)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
              />
            </label>
          </div>
          <p id="research-period-help" className="mt-2 text-[11px] text-[var(--text-muted)]">结束日期留空表示今天。请选择本地数据覆盖范围；缺价仍会明确报错。</p>
          <p className="mt-2 text-[11px] text-[var(--text-muted)]">预处理：{preprocess.winsorize === "mad" ? "MAD去极值" : preprocess.winsorize === "3sigma" ? "3σ去极值" : "不去极值"} · 标准化{preprocess.standardize ? "开" : "关"} · 缺失值处理{preprocess.fillna ? "开" : "关"} · 行业中性{preprocess.industry_neutral ? "开" : "关"} · 市值中性{preprocess.mktcap_neutral ? "开" : "关"}</p>
          {periodError && <p role="alert" className="mt-2 text-xs text-[var(--accent-danger)]">{periodError}</p>}
        </fieldset>
        <ResearchPreprocessControls value={preprocess} onChange={setPreprocess} disabled={task.busy} />
        <ResearchCoveragePanel coverage={currentCoverage} loading={coverageLoading} error={coverageError} disabled={task.busy} onRefresh={() => { coverageController.current?.abort(); setCoverageState({ key: coverageKey, data: null, loading: true, error: "" }); setCoverageRefresh((value) => value + 1) }} onApply={() => { if (currentCoverage?.recommended) editDates(currentCoverage.recommended.start_date, currentCoverage.recommended.end_date) }} />
        <StockResearchInputCheck token={token} scope={scope} query={{ start_date: startDate, end_date: endDate, factor_ids: requiredFactorIds, mktcap_neutral: preprocess.mktcap_neutral, weight: coverageWeight }} disabled={task.busy || catalogLoading || Boolean(catalogError) || Boolean(periodError)} />
        <Tabs value={activeTab} onValueChange={(next) => { if (!currentView.current) setActiveTab(next) }}>
          {!view && <TabsList>
            <TabsTrigger value="analysis" className="text-xs">
              <FlaskConical className="mr-1 h-3.5 w-3.5" />
              因子检验
            </TabsTrigger>
            <TabsTrigger value="backtest" className="text-xs">
              <LineChartIcon className="mr-1 h-3.5 w-3.5" />
              组合回测
            </TabsTrigger>
          </TabsList>}

          
          {(!view || view === "analysis") && <TabsContent value="analysis" className="mt-4 space-y-4">
            <fieldset disabled={task.busy} className="flex flex-wrap items-center gap-3">
              <span className="text-xs text-[var(--text-secondary)]">
                当前因子：
                <b className="text-[var(--text-primary)]">
                  {factors.find((f) => f.id === selected)?.label ?? selected}
                </b>
              </span>
              <label className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
                未来收益周期
                <select
                  value={horizon}
                  onChange={(e) => setHorizon(Number(e.target.value))}
                  className="rounded border border-[var(--border)] bg-[var(--bg-secondary)] px-1.5 py-1 text-xs text-[var(--text-primary)]"
                >
                  <option value={5}>5日</option>
                  <option value={10}>10日</option>
                  <option value={20}>20日</option>
                  <option value={60}>60日</option>
                  {![5, 10, 20, 60].includes(horizon) && <option value={horizon}>{horizon}日（已恢复）</option>}
                </select>
              </label>
              <label className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">分组数量
                <select aria-label="分组数量" value={groups} onChange={(event) => { const next = Number(event.target.value); if (Number.isInteger(next) && next >= 2 && next <= 10) setGroups(next) }} className="rounded border border-[var(--border)] bg-[var(--bg-secondary)] px-1.5 py-1">
                  <option value={5}>5组</option><option value={10}>10组</option>
                  {![5, 10].includes(groups) && <option value={groups}>{groups}组（已恢复）</option>}
                </select>
              </label>
              <button
                onClick={() => void runAnalysis()}
                disabled={!task.availability?.enabled || !task.availability?.accepting || task.recoveryPending || task.blocked || analyzing || running || catalogLoading || Boolean(catalogError) || factors.length === 0 || Boolean(periodError) || coverageBlocked}
                className="flex items-center gap-1.5 rounded-md bg-[var(--primary)] px-3 py-1.5 text-xs text-white hover:opacity-90 disabled:opacity-50 cursor-pointer"
              >
                {analyzing && <Loader2 className="h-3 w-3 animate-spin" />}
                开始检验
              </button>
            </fieldset>

            {view ? <Link href="/backtest" className="text-xs underline">进入组合回测配置</Link> : <><button type="button" onClick={transferToBacktest} disabled={task.busy || task.blocked || task.recoveryPending || catalogLoading || Boolean(catalogError) || !factors.some((factor) => factor.id === selected)} className="rounded border border-[var(--border)] px-3 py-2 text-xs disabled:opacity-40">用当前因子配置回测</button><p className="text-xs text-[var(--text-muted)]">仅将当前因子、共用日期和预处理配置带入回测；不会自动提交任务，也不会改变已保存报告。持股数量、权重、费用仍需在回测中确认。</p></>}
            {(analysisError || analysis?.error) && (
              <p role="alert" className="rounded-md border border-[var(--accent-danger)]/40 bg-[var(--accent-danger)]/10 px-3 py-2 text-xs text-[var(--accent-danger)]">
                {analysisError || analysis?.error}
              </p>
            )}

            {analysis && task.job?.kind === "analysis" && <><ResearchReport job={task.job} result={analysis as unknown as ResearchResult} />{task.availability?.saved_report_artifacts && task.job.status === "completed" && <ResearchReportActions jobId={task.job.id} token={token} scope={scope} />}</>}

            {!analysis && !analyzing && (
              <p className="text-xs text-[var(--text-muted)]">
                选择左侧因子后点击「开始检验」。检验仅使用本地已覆盖样本；财务数据从公告日后的观测日可用，信号再滞后一交易日执行。历史修订与真实交易条件仍待验证。
              </p>
            )}
          </TabsContent>}

          
          {(!view || view === "backtest") && <TabsContent value="backtest" className="mt-4 space-y-4">
            <fieldset disabled={draftLocked} className="rounded-lg border border-[var(--border)] bg-[var(--bg-secondary)] p-4">
              <legend className="px-1 text-xs font-medium">组合策略参数</legend>
              <p className="mb-2 text-xs font-medium text-[var(--text-primary)]">
                回测因子（多选等权合成）
              </p>
              <div className="mb-4 flex flex-wrap gap-2">
                {factors.map((f) => {
                  const active = btFactors.includes(f.id)
                  return (
                    <button
                      key={f.id}
                      type="button"
                      aria-pressed={active}
                      onClick={() => toggleBtFactor(f.id)}
                      className={cn(
                        "rounded-full border px-2.5 py-1 text-[11px] cursor-pointer",
                        active
                          ? "border-[var(--primary)] bg-[var(--primary)]/15 text-[var(--primary)]"
                          : "border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text-primary)]",
                      )}
                    >
                      {f.label}
                    </button>
                  )
                })}
              </div>

              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
                <label className="text-[11px] text-[var(--text-muted)]">
                  调仓频率
                  <select
                    value={rebalance}
                    onChange={(e) => setRebalance(e.target.value)}
                    className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--bg-primary)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
                  >
                    <option value="week">每周</option>
                    <option value="month">每月</option>
                  </select>
                </label>
                <label className="text-[11px] text-[var(--text-muted)]">
                  持股数量
                  <input
                    type="number"
                    min={1}
                    max={300}
                    value={topN}
                    onChange={(e) => setTopN(Number(e.target.value))}
                    className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--bg-primary)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
                  />
                </label>
                <label className="text-[11px] text-[var(--text-muted)]">
                  加权方式
                  <select
                    value={weight}
                    onChange={(e) => setWeight(e.target.value)}
                    className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--bg-primary)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
                  >
                    <option value="equal">等权</option>
                    <option value="mktcap">市值加权</option>
                    <option value="factor">因子秩加权</option>
                  </select>
                </label>
                <label className="text-[11px] text-[var(--text-muted)]">
                  手续费（基点）
                  <input
                    aria-label="手续费（基点）"
                    type="number"
                    min={0}
                    max={100}
                    step="any"
                    value={commissionBps}
                    aria-invalid={Boolean(costError)}
                    aria-describedby="stock-backtest-cost-help"
                    onChange={(e) => { setCommissionBps(e.target.value); setCommission(Number(e.target.value) / 10000) }}
                    className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--bg-primary)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
                  />
                </label>
                <label className="text-[11px] text-[var(--text-muted)]">
                  滑点（基点）
                  <input
                    aria-label="滑点（基点）"
                    type="number"
                    min={0}
                    max={100}
                    step="any"
                    value={slippageBps}
                    aria-invalid={Boolean(costError)}
                    aria-describedby="stock-backtest-cost-help"
                    onChange={(e) => { setSlippageBps(e.target.value); setSlippage(Number(e.target.value) / 10000) }}
                    className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--bg-primary)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
                  />
                </label>
                <div className="flex items-end">
                  <button
                    onClick={() => void runBacktest()}
                    disabled={!task.availability?.enabled || !task.availability?.accepting || draftLocked || running || analyzing || catalogLoading || Boolean(catalogError) || btFactors.length === 0 || Boolean(periodError) || Boolean(costError) || coverageBlocked}
                    className="flex w-full items-center justify-center gap-1.5 rounded-md bg-[var(--primary)] px-3 py-1.5 text-xs text-white hover:opacity-90 disabled:opacity-50 cursor-pointer"
                  >
                    {running && <Loader2 className="h-3 w-3 animate-spin" />}
                    开始回测
                  </button>
                </div>
              </div>
              <p id="stock-backtest-cost-help" className="mt-3 text-[11px] text-[var(--text-muted)]">1基点 = 0.01%，3基点 = 0.03%。提交时换算为比例费率；税费及实际成交限制以报告的方法版本说明为准。</p>
              {costError && <p role="alert" className="mt-2 text-xs text-[var(--accent-danger)]">{costError}</p>}
              <p className="mt-3 text-xs leading-6 text-[var(--text-secondary)]">当前回测草稿：{btFactors.length}个因子 · {topN}只 · {rebalance === "week" ? "每周" : "每月"}调仓 · {weight === "equal" ? "等权" : weight === "mktcap" ? "市值加权" : "因子秩加权"}。{btFactors.map((id) => factors.find((factor) => factor.id === id)?.label ?? id).join("、") || "请选择因子"}</p>
            </fieldset>

            {(backtestError || backtest?.error) && (
              <p role="alert" className="rounded-md border border-[var(--accent-danger)]/40 bg-[var(--accent-danger)]/10 px-3 py-2 text-xs text-[var(--accent-danger)]">
                {backtestError || backtest?.error}
              </p>
            )}

            {backtest && task.job?.kind === "backtest" && <><ResearchReport job={task.job} result={backtest as unknown as ResearchResult} />{task.availability?.saved_report_artifacts && task.job.status === "completed" && <ResearchReportActions jobId={task.job.id} token={token} scope={scope} />}</>}

            {!backtest && !running && (
              <p className="text-xs text-[var(--text-muted)]">
                选择因子与参数后点击「开始回测」。研究以滞后一日信号选股，保持份额并逐日估值，计入比例手续费与滑点。持仓缺价时直接报错；前复权研究不代表停牌、公司行动或真实撮合已经验证。
              </p>
            )}
          </TabsContent>}
        </Tabs>
      </div>
    </div>
  )
}
