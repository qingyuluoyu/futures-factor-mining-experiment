"use client"

import { useEffect, useRef, useState } from "react"
import { getStockEodStatusApi, type StockEodStatusResponse } from "@/lib/stock-eod-status-api"

const labels: Record<StockEodStatusResponse["state"], string> = {
  disabled: "服务停用", unavailable: "状态不可用", manual_only: "仅手动同步", stopped: "调度已停止",
  before_window: "未到盘后窗口", pending: "等待调度", running: "同步进行中", closed: "休市",
  completed: "同步完成", partially_completed: "部分完成", failed: "同步失败", expired: "任务已过期",
}
const archiveLabels: Record<NonNullable<StockEodStatusResponse["archive"]>["state"], string> = {
  disabled: "归档停用", unavailable: "归档不可用", stopped: "归档调度已停止", idle: "归档空闲",
  running: "归档进行中", completed: "归档完成", failed: "归档失败", cancelled: "归档已取消", expired: "归档已过期",
}
const control = "rounded-md border border-[var(--border)] px-3 py-1.5 text-xs hover:bg-[var(--bg-tertiary)]"

export function StockEodStatus({ refreshKey = 0 }: { refreshKey?: number }): React.JSX.Element {
  const [result, setResult] = useState<StockEodStatusResponse | null>(null)
  const [phase, setPhase] = useState<"loading" | "ready" | "error" | "cancelled">("loading")
  const [error, setError] = useState("")
  const [revision, setRevision] = useState(0)
  const activeRead = useRef<AbortController | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    activeRead.current = controller
    setResult(null)
    setError("")
    setPhase("loading")
    void getStockEodStatusApi(controller.signal)
      .then((value) => { if (!controller.signal.aborted) { setResult(value); setPhase("ready") } })
      .catch((failure: unknown) => {
        if (controller.signal.aborted) return
        setError(failure instanceof Error ? failure.name === "TimeoutError" ? "同步状态读取超时，请重试" : failure.message : "同步状态读取失败，请重试")
        setPhase("error")
      })
    return () => controller.abort()
  }, [refreshKey, revision])
  const snapshot = result?.latest_snapshot
  const job = result?.today_job
  const archive = result?.archive
  const archiveJob = archive?.latest_job
  return <section aria-label="盘后同步状态" aria-busy={phase === "loading"} className="space-y-2 rounded-lg border border-[var(--border)] bg-[var(--bg-secondary)] p-3 text-xs text-[var(--text-secondary)]">
    <header className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold text-[var(--text-primary)]">盘后同步状态</h2>
      {phase === "loading" ? <button className={control} onClick={() => { activeRead.current?.abort(); setResult(null); setPhase("cancelled") }}>取消加载</button> : <button className={control} onClick={() => setRevision((value) => value + 1)}>{phase === "error" ? "重试" : "刷新状态"}</button>}
    </header>
    {phase === "loading" && <p role="status">加载盘后同步状态…</p>}
    {phase === "cancelled" && <p role="status">已取消状态读取，可刷新重试。</p>}
    {phase === "error" && <p role="alert" className="text-[var(--accent-danger)]">{error}</p>}
    {result && phase === "ready" && <>
      <p role="status"><strong className={result.state === "failed" || result.state === "expired" || result.state === "unavailable" ? "text-[var(--accent-danger)]" : "text-[var(--text-primary)]"}>{labels[result.state]}</strong> · {result.message}</p>
      <p>北京时间 {result.collection_window} 后进入采集窗口 · 调度检查间隔 {result.schedule_interval_seconds} 秒 · {result.enabled ? "行情服务启用" : "行情服务停用"} / {result.sync_enabled ? "自动同步启用" : "自动同步停用"} / {result.worker_running ? "调度运行" : "调度未运行"}</p>
      <p>服务端时间：<time dateTime={result.server_time}>{result.server_time}</time>（{result.timezone}）</p>
      {result.catchup && <details className="space-y-1"><summary className="cursor-pointer">漏日检查：{result.catchup.window_start} 至 {result.catchup.window_end} · 待日历核验 {result.catchup.pending_dates.length} 日</summary>
        <p>{result.catchup.message}</p>
        <p>{result.catchup.automatic_enabled ? "自动漏日补采已开启（仍受日期/请求预算约束）" : "自动漏日补采未开启；需核对来源与预算后受控启用，状态刷新不触发采集。"}</p>
        <p>待日历核验日期：{result.catchup.pending_dates.join("、") || "无"}；不能据此认定为交易日行情缺口。</p>
        <p>已失败日期：{result.catchup.failed_dates.join("、") || "无"} · 已过期日期：{result.catchup.expired_dates.join("、") || "无"}；失败不自动重试。</p>
        <p>每轮最多{result.catchup.max_dates_per_cycle}日，每日最多{result.catchup.max_source_calls_per_date}次来源请求；窗口外历史需受控补采。</p>
      </details>}
      {job && <details className="space-y-1"><summary className="cursor-pointer">今日任务 {job.trade_date} · {job.status} · 步骤 {job.step || "未开始"} · 尝试 {job.attempts} 次</summary>
        <p className="break-all">任务ID：{job.id}</p>
        <p>开始：{job.started_at ?? "尚未开始"} · 截止：{job.deadline_at ?? "未提供"} · 结束：{job.finished_at ?? "尚未结束"}</p>
        {job.error_code && <p className="text-[var(--accent-danger)]">错误类型：{job.error_code}</p>}
      </details>}
      {snapshot ? <div className="space-y-1 border-t border-[var(--border)] pt-2">
        {snapshot.recovery_applied && <p>缓存人工恢复1次；原采集尝试及原失败已保留审计，未重复下载、未追认独立认证。</p>}
        <p>最近已存快照交易日：<strong className="font-num">{snapshot.trade_date}</strong> · 来源 {snapshot.provider} · {snapshot.price_adjustment === "none" ? "不复权" : snapshot.price_adjustment === "qfq" ? "前复权" : snapshot.price_adjustment}</p>
        <p>行情覆盖 {snapshot.coverage.quoted} / {snapshot.coverage.expected} · 目录缺行情 {snapshot.coverage.missing} 只 · 目录未匹配 {snapshot.coverage.unknown} 只</p>
        <p>{snapshot.raw_response_evidence ? "已存原始响应证据" : "原始响应证据缺失"}</p>
        <details className="space-y-1"><summary className="cursor-pointer">查看快照元数据</summary><p className="break-all">数据版本：<code>{snapshot.data_version}</code></p><p>采集时间：{snapshot.collected_at ?? "未提供"}</p></details>
      </div> : <p>暂无已存盘后快照；未生成当日收盘行情。</p>}
      <p className="text-[11px] leading-5 text-[var(--text-muted)]">仅展示已存任务与快照元数据，不代表独立核验或交易所认证；快照日期不等于数据已更新至当前交易日。</p>
      <div aria-label="影子归档状态" className="space-y-1 border-t border-[var(--border)] pt-2">
        <h3 className="font-semibold text-[var(--text-primary)]">影子归档状态</h3>
        {archive ? <>
          <p role="status"><strong className={archive.state === "failed" || archive.state === "expired" || archive.state === "unavailable" ? "text-[var(--accent-danger)]" : "text-[var(--text-primary)]"}>{archiveLabels[archive.state]}</strong> · {archive.message}</p>
          <p>{archive.enabled ? "归档开关启用" : "归档开关停用"} / {archive.ready ? "归档就绪" : "归档未就绪"} / {archive.worker_running ? "归档worker运行" : "归档worker未运行"}</p>
          {archiveJob ? <details className="space-y-1"><summary className="cursor-pointer">最近归档任务 · 来源交易日 {archiveJob.trade_date} · {archiveJob.status} · 步骤 {archiveJob.step} · 尝试 {archiveJob.attempts} 次{archiveJob.error_code ? ` · 错误类型 ${archiveJob.error_code}` : ""}</summary>
            <p className="break-all">归档任务ID：{archiveJob.id} · 请求ID：{archiveJob.request_id}</p>
            <p className="break-all">来源批次ID：{archiveJob.source_batch_id}</p>
            <p className="break-all">来源数据版本：<code>{archiveJob.source_data_version}</code></p>
            <p>开始：{archiveJob.started_at} · 更新：{archiveJob.updated_at} · 截止：{archiveJob.deadline_at} · 结束：{archiveJob.finished_at ?? "尚未结束"}</p>
            {archiveJob.archive_batch_id && <p className="break-all">影子归档批次ID：{archiveJob.archive_batch_id}</p>}
          </details> : <p>暂无归档任务。</p>}
          <p className="text-[11px] leading-5 text-[var(--text-muted)]">仅影子归档；未迁移旧qfq历史，用户行情读路径未改变，不代表独立核验或交易所认证。</p>
        </> : <p>旧服务未提供归档状态；归档进度未知。</p>}
      </div>
    </>}
    <p className="text-[11px] text-[var(--text-muted)]">刷新只读取状态，不触发行情采集。</p>
  </section>
}
