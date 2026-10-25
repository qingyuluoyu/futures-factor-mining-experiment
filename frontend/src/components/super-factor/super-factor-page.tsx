"use client"

/**
 * 超级因子挖掘主页面 —— 长程后台因子挖掘
 * 左：配置表单 + 任务列表；右：选中任务的进度/冠军结果
 */

import { useEffect, useState } from "react"
import { Loader2, Pause, Play, Plus, Square, Trash2, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"
import { ChampionTable } from "@/components/factor-lab/champion-table"
import { addFactorFavorite, listFactorFavorites } from "@/lib/factor-lab-api"
import type { Champion } from "@/lib/factor-lab-api"
import { showAlert } from "@/stores/dialog"
import {
  fetchSupported,
  type SupportedInfo,
  type SupportedTimeframe,
} from "@/lib/super-factor-api"
import { MiningSymbolCombobox } from "./mining-symbol-combobox"
import { useMiningTasks, useTaskChampions } from "./use-mining-tasks"

const STATUS_STYLE: Record<string, string> = {
  pending: "bg-yellow-500/15 text-yellow-500",
  running: "bg-blue-500/15 text-blue-500",
  paused: "bg-gray-500/15 text-gray-400",
  completed: "bg-emerald-500/15 text-emerald-500",
  failed: "bg-red-500/15 text-red-500",
  cancelled: "bg-gray-500/15 text-gray-400",
}
const STATUS_LABEL: Record<string, string> = {
  pending: "待运行",
  running: "运行中",
  paused: "已暂停",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
}

export function SuperFactorPage(): React.JSX.Element {
  const {
    tasks,
    loading,
    error,
    createTask,
    pauseTask,
    resumeTask,
    cancelTask,
    removeTask,
  } = useMiningTasks()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [supported, setSupported] = useState<SupportedInfo | null>(null)

  useEffect(() => {
    void (async () => {
      try {
        const info = await fetchSupported()
        setSupported(info)
      } catch {
        // 忽略，表单仍可用
      }
    })()
  }, [])

  return (
    <div className="p-4 space-y-4 max-w-[1400px] mx-auto">
      <div>
        <h1 className="text-lg font-semibold text-[var(--text-primary)]">
          超级因子挖掘
        </h1>
        <p className="text-xs text-[var(--text-muted)] mt-1">
          查看挖掘任务与已保存结果。历史覆盖取决于当前数据源，日线与分钟线不能互相补造。
        </p>
        <p role="alert" className="mt-2 rounded border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-[var(--text-secondary)]">
          生产任务执行器尚未接通检查点；连续种群演化、固定输入快照与可验证断点恢复仍在修复。新建及恢复暂不开放，当前进度和已存结果不代表研究能力已验收，请勿通过增加代数反复尝试。
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        {/* 左侧：配置 + 任务列表 */}
        <div className="lg:col-span-2 space-y-4">
          <MiningConfigForm
            supported={supported}
            disabled={loading || supported?.accepting !== true}
            onSubmit={async (p) => {
              try {
                await createTask(p)
              } catch {
                /* hook 已 setError */
              }
            }}
          />
          {error && (
            <div className="text-xs text-red-500 px-2">{error}</div>
          )}
          <TaskListPanel
            tasks={tasks}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onPause={pauseTask}
            onResume={resumeTask}
            canResume={supported?.exact_resume_available === true}
            onCancel={cancelTask}
            onDelete={removeTask}
          />
        </div>

        {/* 右侧：选中任务详情 */}
        <div className="lg:col-span-3">
          {selectedId ? (
            <TaskDetailPanel
              taskId={selectedId}
              tasks={tasks}
              onClose={() => setSelectedId(null)}
            />
          ) : (
            <div className="rounded-xl border border-dashed border-[var(--border)] p-8 text-center text-sm text-[var(--text-muted)] h-full flex items-center justify-center">
              从左侧选择任务查看进度与结果
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── 配置表单 ──────────────────────────────────────────────

interface ConfigFormProps {
  supported: SupportedInfo | null
  disabled: boolean
  onSubmit: (p: {
    symbol: string
    timeframe: string
    population: number
    generations: number
    max_depth: number
    train_ratio: number
    walk_forward_folds: number
    device: string
    name: string
  }) => Promise<void>
}

function MiningConfigForm(props: ConfigFormProps): React.JSX.Element {
  const { supported, disabled, onSubmit } = props
  const [symbol, setSymbol] = useState("")
  const [timeframe, setTimeframe] = useState("1d")
  // 数字输入用 string 存原始文本，让用户能自由编辑（清空、临时非法值）；
  // onBlur 和提交时才解析+clamp 到合法范围。避免输入过程中被立即 clamp 的卡顿。
  const [population, setPopulation] = useState("40")
  const [generations, setGenerations] = useState("30")
  const [maxDepth, setMaxDepth] = useState("4")
  const [trainRatio, setTrainRatio] = useState("0.7")
  const [walkForwardFolds, setWalkForwardFolds] = useState("3")
  const [submitting, setSubmitting] = useState(false)

  const tfs: SupportedTimeframe[] = supported?.timeframes ?? [
    { value: "1d", label: "日线", long_history: true, note: "" },
  ]
  const curTf = tfs.find((t) => t.value === timeframe)

  /** 解析输入框值为整数并 clamp 到 [lo,hi]；空/非法返回 def */
  function clampInt(s: string, lo: number, hi: number, def: number): number {
    const n = parseInt(s, 10)
    if (Number.isNaN(n)) return def
    return Math.max(lo, Math.min(hi, n))
  }
  /** 解析输入框值为浮点并 clamp 到 [lo,hi]；空/非法返回 def */
  function clampFloat(s: string, lo: number, hi: number, def: number): number {
    const n = parseFloat(s)
    if (Number.isNaN(n)) return def
    return Math.max(lo, Math.min(hi, n))
  }

  const [symbolError, setSymbolError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!symbol.trim()) {
      setSymbolError("请先选择品种（不再默认螺纹钢）")
      return
    }
    setSymbolError(null)
    setSubmitting(true)
    try {
      await onSubmit({
        symbol: symbol.trim().toLowerCase(),
        timeframe,
        population: clampInt(population, 10, 200, 40),
        generations: clampInt(generations, 3, 200, 30),
        max_depth: clampInt(maxDepth, 2, 6, 4),
        train_ratio: clampFloat(trainRatio, 0, 0.9, 0.7),
        walk_forward_folds: clampInt(walkForwardFolds, 0, 6, 3),
        device: "cpu",
        name: `超挖·${symbol}·${timeframe}`,
      })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-xl border border-[var(--border)] bg-[var(--bg-secondary)] p-4 space-y-3"
    >
      <div className="text-sm font-medium text-[var(--text-secondary)]">
        新建挖掘任务
      </div>

      <div className="space-y-1.5">
        <Label>品种</Label>
        <MiningSymbolCombobox
          value={symbol}
          onChange={(v) => {
            setSymbol(v)
            if (v.trim()) setSymbolError(null)
          }}
        />
        {symbolError && <p className="text-[11px] text-down">{symbolError}</p>}
      </div>

      <div className="space-y-1">
        <Label>周期</Label>
        <div className="flex flex-wrap gap-1">
          {tfs.map((tf) => (
            <button
              key={tf.value}
              type="button"
              onClick={() => setTimeframe(tf.value)}
              className={cn(
                "px-2 py-1 rounded text-[11px] border transition-colors",
                timeframe === tf.value
                  ? "border-[var(--primary)] bg-[var(--primary)]/15 text-[var(--primary)]"
                  : "border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text-primary)]",
              )}
              title={tf.note}
            >
              {tf.label}
              {tf.long_history && (
                <span className="ml-1 text-[9px] text-emerald-500">超长</span>
              )}
            </button>
          ))}
        </div>
        {curTf && !curTf.long_history && (
          <p className="text-[10px] text-amber-500">{curTf.note}</p>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <Label>种群（10-200）</Label>
          <p className="text-[10px] text-[var(--text-muted)] leading-tight">
            每一代同时尝试多少个因子公式。越大搜索面越广但越慢，建议 40。
          </p>
          <input
            type="number"
            inputMode="numeric"
            value={population}
            onChange={(e) => setPopulation(e.target.value)}
            onBlur={() => setPopulation(String(clampInt(population, 10, 200, 40)))}
            placeholder="40"
            className="w-full h-9 px-3 text-xs font-num rounded-md border border-[var(--border)] bg-[var(--bg-primary)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--primary)]"
          />
        </div>
        <div className="space-y-1">
          <Label>代数（3-200）</Label>
          <p className="text-[10px] text-[var(--text-muted)] leading-tight">
            迭代多少轮。每轮保留好因子再衍生新公式，越多越易找到优解但越慢，建议 30。
          </p>
          <input
            type="number"
            inputMode="numeric"
            value={generations}
            onChange={(e) => setGenerations(e.target.value)}
            onBlur={() => setGenerations(String(clampInt(generations, 3, 200, 30)))}
            placeholder="30"
            className="w-full h-9 px-3 text-xs font-num rounded-md border border-[var(--border)] bg-[var(--bg-primary)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--primary)]"
          />
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="space-y-1">
          <Label>树深（2-6）</Label>
          <p className="text-[10px] text-[var(--text-muted)] leading-tight">
            因子公式最复杂能到几层嵌套。越深越灵活但越易过拟合，建议 4。
          </p>
          <input
            type="number"
            inputMode="numeric"
            value={maxDepth}
            onChange={(e) => setMaxDepth(e.target.value)}
            onBlur={() => setMaxDepth(String(clampInt(maxDepth, 2, 6, 4)))}
            placeholder="4"
            className="w-full h-9 px-3 text-xs font-num rounded-md border border-[var(--border)] bg-[var(--bg-primary)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--primary)]"
          />
        </div>
        <div className="space-y-1">
          <Label>训练比例（0-0.9）</Label>
          <p className="text-[10px] text-[var(--text-muted)] leading-tight">
            历史数据按多大比例做训练，剩下留作验证。0.7 = 前70%训练、后30%验证，建议 0.7。
          </p>
          <input
            type="number"
            inputMode="decimal"
            step={0.05}
            value={trainRatio}
            onChange={(e) => setTrainRatio(e.target.value)}
            onBlur={() => setTrainRatio(String(clampFloat(trainRatio, 0, 0.9, 0.7)))}
            placeholder="0.7"
            className="w-full h-9 px-3 text-xs font-num rounded-md border border-[var(--border)] bg-[var(--bg-primary)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--primary)]"
          />
        </div>
        <div className="space-y-1">
          <Label>WF折数（0-6）</Label>
          <p className="text-[10px] text-[var(--text-muted)] leading-tight">
            把历史切几段滚动验证，要求每段都赚钱才算数，越严越不易过拟合。0=关闭，建议 3。
          </p>
          <input
            type="number"
            inputMode="numeric"
            value={walkForwardFolds}
            onChange={(e) => setWalkForwardFolds(e.target.value)}
            onBlur={() =>
              setWalkForwardFolds(String(clampInt(walkForwardFolds, 0, 6, 3)))
            }
            placeholder="3"
            className="w-full h-9 px-3 text-xs font-num rounded-md border border-[var(--border)] bg-[var(--bg-primary)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--primary)]"
          />
        </div>
      </div>

      <p className="text-[10px] text-[var(--text-muted)] leading-relaxed bg-[var(--bg-tertiary)] rounded-md p-2">
        💡 不知道怎么填？用默认值即可（种群40/代数30/树深4/训练比0.7/WF3）。
        想更快出结果：代数降到 15、WF 改 0；想挖更优：代数提到 50、种群提到 80（耗时更长）。
        超长历史（几千根）建议先关 WF（设0）避免计算过重。
      </p>

      <Button type="submit" disabled={disabled || submitting} className="w-full">
        {submitting ? (
          <Loader2 className="w-4 h-4 mr-1 animate-spin" />
        ) : (
          <Plus className="w-4 h-4 mr-1" />
        )}
        开始挖掘
      </Button>
    </form>
  )
}

// ── 任务列表 ──────────────────────────────────────────────

interface TaskListProps {
  tasks: ReturnType<typeof useMiningTasks>["tasks"]
  selectedId: string | null
  onSelect: (id: string) => void
  onPause: (id: string) => void
  onResume: (id: string) => void
  canResume: boolean
  onCancel: (id: string) => void
  onDelete: (id: string) => void
}

function TaskListPanel(props: TaskListProps): React.JSX.Element {
  const { tasks, selectedId, onSelect, onPause, onResume, onCancel, onDelete, canResume } =
    props
  if (tasks.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-[var(--border)] p-6 text-center text-xs text-[var(--text-muted)]">
        暂无挖掘任务
      </div>
    )
  }
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-secondary)] divide-y divide-[var(--border)] overflow-hidden">
      {tasks.map((t) => {
        const on = selectedId === t.id
        const active = t.status === "running" || t.status === "pending"
        return (
          <div
            key={t.id}
            onClick={() => onSelect(t.id)}
            className={cn(
              "p-3 cursor-pointer transition-colors",
              on ? "bg-[var(--primary)]/10" : "hover:bg-[var(--bg-tertiary)]",
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="text-xs font-medium text-[var(--text-primary)] truncate">
                  {t.name}
                </div>
                <div className="text-[10px] text-[var(--text-muted)] font-num">
                  {t.symbol} · {t.timeframe} · {t.bars_count}根
                </div>
              </div>
              <span
                className={cn(
                  "px-1.5 py-0.5 rounded text-[10px] shrink-0",
                  STATUS_STYLE[t.status],
                )}
              >
                {STATUS_LABEL[t.status]}
              </span>
            </div>
            {/* 进度条 */}
            <div className="mt-2 h-1.5 rounded-full bg-[var(--bg-tertiary)] overflow-hidden">
              <div
                className="h-full bg-[var(--primary)] transition-all"
                style={{ width: `${t.progress_pct}%` }}
              />
            </div>
            <div className="mt-1 flex items-center justify-between text-[10px] text-[var(--text-muted)] font-num">
              <span>
                代 {t.current_generation}/{t.generations}
              </span>
              <span>{t.progress_pct}%</span>
            </div>
            {/* 操作按钮 */}
            <div
              className="mt-2 flex gap-1"
              onClick={(e) => e.stopPropagation()}
            >
              {active && (
                <button
                  type="button"
                  onClick={() => onPause(t.id)}
                  className="px-1.5 py-0.5 rounded text-[10px] border border-[var(--border)] hover:bg-[var(--bg-tertiary)] flex items-center gap-0.5"
                >
                  <Pause className="w-2.5 h-2.5" /> 暂停
                </button>
              )}
              {t.status === "paused" && (
                <button
                  type="button"
                  onClick={() => onResume(t.id)}
                  disabled={!canResume}
                  className="px-1.5 py-0.5 rounded text-[10px] border border-[var(--border)] hover:bg-[var(--bg-tertiary)] flex items-center gap-0.5"
                >
                  <Play className="w-2.5 h-2.5" /> 恢复
                </button>
              )}
              {!["completed", "cancelled", "failed"].includes(t.status) && (
                <button
                  type="button"
                  onClick={() => onCancel(t.id)}
                  className="px-1.5 py-0.5 rounded text-[10px] border border-[var(--border)] hover:bg-[var(--bg-tertiary)] flex items-center gap-0.5"
                >
                  <Square className="w-2.5 h-2.5" /> 取消
                </button>
              )}
              {!active && (
                <button
                  type="button"
                  onClick={() => onDelete(t.id)}
                  className="px-1.5 py-0.5 rounded text-[10px] border border-red-500/30 text-red-500 hover:bg-red-500/10 flex items-center gap-0.5"
                >
                  <Trash2 className="w-2.5 h-2.5" /> 删除
                </button>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── 任务详情（进度 + 冠军结果） ──────────────────────────

interface DetailProps {
  taskId: string
  tasks: ReturnType<typeof useMiningTasks>["tasks"]
  onClose: () => void
}

function TaskDetailPanel(props: DetailProps): React.JSX.Element {
  const { taskId, tasks, onClose } = props
  const task = tasks.find((t) => t.id === taskId)
  const { champions, loading: championsLoading, error: championsError, retry: retryChampions } = useTaskChampions(
    task?.status === "completed" ? taskId : null,
  )

  // 已收藏 tokens_key 集合（冠军表行变灰、按钮禁用，避免重复收藏）
  const [favoritedKeys, setFavoritedKeys] = useState<Set<string>>(new Set())
  useEffect(() => {
    if (task?.status !== "completed") {
      setFavoritedKeys(new Set())
      return
    }
    let cancelled = false
    void (async () => {
      try {
        const favs = await listFactorFavorites(task.symbol)
        if (cancelled) return
        const set = new Set<string>()
        for (const f of favs) {
          if (f.tokens?.length) set.add(f.tokens.join(","))
        }
        setFavoritedKeys(set)
      } catch {
        // 收藏列表加载失败不阻塞查看冠军
      }
    })()
    return () => {
      cancelled = true
    }
  }, [task?.status, task?.symbol, taskId])

  // 收藏冠军因子（复用 factor_lab 收藏 API，两个页面因子库打通）
  async function handleFavorite(c: Champion): Promise<void> {
    if (!task) return
    const key = c.tokens.join(",")
    // 本地立即置灰，避免重复点击
    setFavoritedKeys((prev) => new Set(prev).add(key))
    try {
      await addFactorFavorite({
        tokens: c.tokens,
        text: c.text,
        symbol: task.symbol,
        timeframe: task.timeframe,
        composite: c.composite,
        metrics: c.metrics,
        name: `超挖·${task.symbol}·${task.timeframe}`,
      })
      await showAlert({ title: "已收藏", description: "可在因子实验室收藏面板查看" })
    } catch (e) {
      // 失败回退本地置灰
      setFavoritedKeys((prev) => {
        const next = new Set(prev)
        next.delete(key)
        return next
      })
      await showAlert({
        title: "收藏失败",
        description: e instanceof Error ? e.message : "收藏失败",
        variant: "destructive",
      })
    }
  }

  if (!task) {
    return (
      <div className="rounded-xl border border-dashed border-[var(--border)] p-8 text-center text-sm text-[var(--text-muted)]">
        任务不存在
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-secondary)] p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-medium text-[var(--text-primary)]">
            {task.name}
          </div>
          <div className="text-[11px] text-[var(--text-muted)] font-num">
            {task.symbol} · {task.timeframe} · 数据 {task.data_range_from} ~{" "}
            {task.data_range_to} ({task.bars_count}根)
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="text-[var(--text-muted)] hover:text-[var(--text-primary)]"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* 进度 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
        <Stat label="状态" value={STATUS_LABEL[task.status]} />
        <Stat
          label="进度"
          value={`${task.current_generation}/${task.generations} 代 (${task.progress_pct}%)`}
        />
        <Stat
          label="当前最优分"
          value={task.best_composite.toFixed(2)}
        />
        <Stat label="冠军数" value={String(task.champions_count)} />
      </div>

      {/* 错误信息 */}
      {task.error_msg && (
        <div className="text-xs text-red-500 bg-red-500/10 rounded p-2">
          {task.error_msg}
        </div>
      )}

      {/* 冠军结果（仅完成时） */}
      {task.status === "completed" && (
        <div className="space-y-2">
          <div className="text-xs text-[var(--text-secondary)]">
            冠军因子（含训练/测试双指标）
          </div>
          {championsLoading ? (
            <p role="status" className="text-xs text-[var(--text-muted)]">正在读取已保存的冠军结果…</p>
          ) : championsError ? (
            <div role="alert" className="rounded border border-red-500/30 p-3 text-xs text-red-500">
              <p>冠军结果读取失败：{championsError}。这不代表任务没有合格因子。</p>
              <button type="button" onClick={retryChampions} className="mt-2 rounded border border-current px-2 py-1">重新读取</button>
            </div>
          ) : champions.length === 0 ? (
            <p className="text-xs text-[var(--text-muted)]">结果读取成功，本次任务没有保存符合筛选条件的冠军因子；不是可交易策略认证。</p>
          ) : (
            <ChampionTable champions={champions} selectedTokens={null} onSelect={() => {}} onFavorite={handleFavorite} favoritedKeys={favoritedKeys} />
          )}
        </div>
      )}

      {task.status !== "completed" && (
        <div className="text-xs text-[var(--text-muted)] text-center py-4">
          {task.status === "running" || task.status === "pending"
            ? "任务待调度或执行中；非交易时段当前后台暂停推进。完成后才能读取结果，进度不代表可交易策略认证。"
            : task.status === "failed"
              ? "任务失败"
              : "任务已停止"}
        </div>
      )}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }): React.JSX.Element {
  return (
    <div className="rounded-lg border border-[var(--border)] p-2">
      <div className="text-[10px] text-[var(--text-muted)]">{label}</div>
      <div className="text-sm font-num text-[var(--text-primary)]">{value}</div>
    </div>
  )
}
