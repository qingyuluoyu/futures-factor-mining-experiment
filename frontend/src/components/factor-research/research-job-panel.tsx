"use client"

import Link from "next/link"
import { isActiveResearchJob, type ResearchJob } from "@/lib/stock-research-api"
import type { ResearchTaskState } from "./use-research-task"

export const researchStatusLabels: Record<ResearchJob["status"], string> = { pending: "排队中", running: "运行中", completed: "已完成", failed: "失败", cancelled: "已取消", expired: "已过期" }

export function ResearchJobMetadata({ job }: { job: ResearchJob }): React.JSX.Element {
  return <section className="space-y-2 rounded border border-[var(--border)] p-3 text-xs">
    <p className="font-medium">{job.title} · {researchStatusLabels[job.status]} · {job.progress}% · {job.step}</p>
    <p className="break-all">任务ID：{job.id} · 请求ID：{job.request_id}</p>
    <p>创建：{job.created_at} · 更新：{job.updated_at} · 执行次数：{job.attempts}</p>
    <p className="break-all">方法版本：{job.method_version} · 流程版本：{job.workflow_version} · 数据版本：{job.data_version ?? "尚未生成"}</p>
    {job.error && <p role="alert">{job.error.code}：{job.error.message}（{job.error.status}）</p>}
    {job.params && <details><summary>研究参数</summary><pre className="overflow-auto whitespace-pre-wrap">{JSON.stringify(job.params, null, 2)}</pre></details>}
  </section>
}

export function ResearchJobPanel({ task }: { task: ResearchTaskState }): React.JSX.Element {
  return <section className="mb-4 space-y-2 text-xs text-[var(--text-secondary)]">
    {!task.availability ? <p role="status">正在检查研究任务服务…</p> : !task.availability.enabled ? <p role="status">研究任务未启用：{task.availability.reason || "管理员尚未开启服务"}</p> : !task.availability.accepting ? <p role="status">暂不接收新任务：{task.availability.reason || "计算服务不可用"}</p> : <p>任务在后台运行；刷新后可恢复，历史报告保留结果和失败原因。</p>}
    {task.job && <ResearchJobMetadata job={task.job} />}
    {task.recoveryPending && <p role="status">正在核对任务恢复记录；确认前暂停新建和配置回填。若查询失败，请使用“重试查询或原提交”核对，不会丢弃未知提交。</p>}
    {task.storageError && <p role="alert">{task.storageError}</p>}
    <div className="flex gap-4">
      {isActiveResearchJob(task.job) && <button type="button" disabled={task.cancelling} onClick={() => void task.cancel()} className="underline disabled:opacity-50">{task.cancelling ? "取消处理中…" : "取消研究任务"}</button>}
      <button type="button" disabled={task.refreshingAvailability} onClick={() => void task.refreshAvailability()} className="underline disabled:opacity-50">刷新服务状态</button>
      {(task.error || !task.availability) && <button type="button" onClick={() => void task.retry()} className="underline">重试查询或原提交</button>}
      <Link href="/history" className="underline">历史研究报告</Link>
    </div>
  </section>
}
