"use client"

import { useEffect, useState } from "react"
import { BRAND_NAME } from "@/lib/brand"
import { DashboardFrame } from "@/components/dashboard/dashboard-frame"

interface LoginDashboardSummary {
  collectedAt: string
  taskCount: number
  runningCount: number
  pausedCount: number
}

type PreviewState =
  | { status: "loading" }
  | { status: "ready"; summary: LoginDashboardSummary }
  | { status: "failed"; message: string }

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "")

export async function fetchLoginDashboardSummary(signal?: AbortSignal): Promise<LoginDashboardSummary> {
  const controller = new AbortController()
  const cancel = (): void => controller.abort()
  if (signal?.aborted) throw new Error("公开数据读取已取消")
  signal?.addEventListener("abort", cancel, { once: true })
  let timedOut = false
  const timeout = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, 8000)

  try {
    const response = await fetch(`${API_BASE}/api/showcase/ai-trading?limit=4`, {
      credentials: "omit",
      cache: "no-store",
      signal: controller.signal,
    })
    if (!response.ok) throw new Error(`公开数据暂不可用（HTTP ${response.status}）`)
    const payload: unknown = await response.json().catch(() => { throw new Error("公开数据格式异常") })
    if (!payload || typeof payload !== "object") throw new Error("公开数据格式异常")
    const data = payload as Record<string, unknown>
    if (
      typeof data.updated_at !== "string"
      || !/T.+(?:Z|[+-]\d{2}:\d{2})$/.test(data.updated_at)
      || !Number.isFinite(Date.parse(data.updated_at))
      || !Array.isArray(data.tasks)
      || data.tasks.length > 4
      || !Number.isInteger(data.running_count)
    ) throw new Error("公开数据格式异常")

    let runningCount = 0
    let pausedCount = 0
    for (const task of data.tasks) {
      if (!task || typeof task !== "object") throw new Error("公开数据格式异常")
      if (task.status === "running") runningCount++
      else if (task.status === "paused") pausedCount++
      else throw new Error("公开数据格式异常")
    }
    if (runningCount !== data.running_count) throw new Error("公开数据格式异常")
    return { collectedAt: data.updated_at, taskCount: data.tasks.length, runningCount, pausedCount }
  } catch (error) {
    if (timedOut) throw new Error("公开数据请求超时")
    if (error instanceof Error && error.message.startsWith("公开数据")) throw error
    throw new Error("公开数据连接失败")
  } finally {
    clearTimeout(timeout)
    signal?.removeEventListener("abort", cancel)
  }
}

export function LoginDashboardBackground(): React.JSX.Element {
  const [state, setState] = useState<PreviewState>({ status: "loading" })

  useEffect(() => {
    const controller = new AbortController()
    void fetchLoginDashboardSummary(controller.signal).then(
      (summary) => { if (!controller.signal.aborted) setState({ status: "ready", summary }) },
      (error: unknown) => {
        if (!controller.signal.aborted) setState({
          status: "failed",
          message: error instanceof Error ? error.message : "公开数据暂不可用",
        })
      },
    )
    return () => controller.abort()
  }, [])

  const summary = state.status === "ready" ? state.summary : null
  const statusText = state.status === "loading" ? "正在读取公开数据"
    : state.status === "failed" ? state.message
      : summary?.taskCount === 0 ? "暂无公开运行数据" : "公开运行状态快照"
  const metrics = [
    ["公开样本", summary?.taskCount],
    ["正在运行", summary?.runningCount],
    ["已暂停", summary?.pausedCount],
  ] as const

  const locked = (label: string) => <div className="flex min-h-24 items-center justify-center text-xs text-[var(--text-muted)]">{label} · 登录后查看</div>
  return <>
    <div aria-hidden="true" inert className="pointer-events-none absolute inset-0 select-none overflow-hidden bg-[var(--bg-primary)] blur-[1.5px]">
      <header className="flex h-14 items-center justify-between border-b border-[var(--border)] bg-[var(--bg-secondary)] px-5">
        <div className="flex items-center gap-3"><span className="flex h-8 w-8 items-center justify-center rounded-lg bg-[var(--primary)] font-semibold text-white">闻</span><strong className="text-sm">{BRAND_NAME}</strong></div>
        <span className="text-xs text-[var(--text-muted)]">市场行情模拟</span>
      </header>
      <div className="flex h-[calc(100%_-_3.5rem)]">
        <aside className="hidden w-[192px] shrink-0 border-r border-[var(--border)] bg-[var(--bg-secondary)] py-2 lg:block">
          {["工作台", "行情", "AI看盘行情", "交易", "AI交易", "因子研究", "历史报告", "个人中心"].map((label, index) => <div key={label} className={`mx-1.5 rounded-md px-3 py-2.5 text-sm ${index === 0 ? "bg-[var(--primary)]/20 text-[var(--primary)]" : "text-[var(--text-secondary)]"}`}>{label}</div>)}
        </aside>
        <main className="min-w-0 flex-1">
          <DashboardFrame
            channel={<div className="border-b border-[var(--border)] px-4 py-2 text-xs text-[var(--text-muted)]">工作台 · 公开预览</div>}
            assets={<div className="grid grid-cols-2 gap-4 border-b border-[var(--border)] px-4 py-3 md:grid-cols-4">{["账户权益", "可用保证金", "浮动盈亏", "累计收益"].map((label) => <div key={label}><p className="text-xs text-[var(--text-muted)]">{label}</p><p className="mt-1 font-num text-lg">—</p><p className="text-[10px] text-[var(--text-muted)]">登录后查看</p></div>)}</div>}
            watchlist={<div className="min-h-56"><div className="grid grid-cols-4 border-b border-[var(--border)] py-2 text-xs text-[var(--text-muted)]"><span>名称 / 代码</span><span>最新价</span><span>涨跌幅</span><span>成交量</span></div>{locked("我的自选行情")}</div>}
            positions={locked("持仓明细")}
            trades={locked("成交记录")}
            ai={<div className="space-y-3"><p className="text-xs text-[var(--text-muted)]">{statusText}</p><div className="grid grid-cols-3 gap-4">{metrics.map(([label, value]) => <div key={label}><p className="text-xs text-[var(--text-muted)]">{label}</p><p className="mt-1 font-num text-xl">{value ?? "—"}</p></div>)}</div><p className="text-xs text-[var(--text-muted)]">采集时间（北京时间）：{summary ? <time dateTime={summary.collectedAt}>{new Date(summary.collectedAt).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })}</time> : "未取得"} · 仅展示脱敏公开任务统计</p></div>}
          />
        </main>
      </div>
    </div>
    <p role="status" className="pointer-events-none absolute inset-x-4 top-[4.5rem] z-20 text-center text-[10px] text-[var(--text-secondary)] md:left-auto md:right-7 md:top-5 md:text-right">公开预览 · {statusText}</p>
  </>
}
