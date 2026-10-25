"use client"

import { useEffect, useRef, useState } from "react"
import { useAuthStore } from "@/stores/auth"
import { stockResearchScope } from "@/lib/stock-research-api"
import { compareStockResearchReports, type SavedResearchComparison } from "@/lib/stock-report-artifacts"
import { MiniLineChart } from "./mini-line-chart"

function valueLabel(value: number | null, unit: string, delta = false): string {
  if (value === null) return delta ? "不可比 / 未记录" : "未记录"
  if (unit === "percent") return `${value.toFixed(2)}${delta ? " 个百分点" : "%"}`
  return unit === "count" ? String(value) : value.toFixed(4)
}

export function ResearchComparisonPanel({ leftId, rightId, token, scope, onClear }: { leftId?: string; rightId?: string; token: string; scope: string; onClear: () => void }): React.JSX.Element {
  const revision = useAuthStore((state) => state.sessionRevision)
  const [report, setReport] = useState<SavedResearchComparison | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const mounted = useRef(false)
  const controller = useRef<AbortController | null>(null)
  const inflight = useRef(false)
  const current = (request: AbortController, session: number) => {
    const auth = useAuthStore.getState()
    return mounted.current && controller.current === request && !request.signal.aborted && auth.accessToken === token && auth.sessionRevision === session && auth.user?.market_type === "stock" && stockResearchScope(auth.user.id, token) === scope
  }
  useEffect(() => {
    mounted.current = true
    inflight.current = false
    setReport(null); setError(""); setBusy(false)
    return () => { mounted.current = false; controller.current?.abort() }
  }, [leftId, rightId, token, scope, revision])

  const compare = async () => {
    if (!leftId || !rightId || leftId === rightId || inflight.current) return
    const request = new AbortController()
    controller.current = request
    const session = useAuthStore.getState().sessionRevision
    if (!current(request, session)) return
    inflight.current = true
    setBusy(true); setError(""); setReport(null)
    try {
      const result = await compareStockResearchReports(leftId, rightId, token, request.signal)
      if (current(request, session)) setReport(result)
    } catch (failure) {
      if (current(request, session)) setError(failure instanceof Error ? failure.message : "保存报告对照失败")
    } finally {
      if (current(request, session)) { setBusy(false); inflight.current = false }
    }
  }

  return <section className="space-y-3 rounded border border-[var(--border)] p-3 text-xs">
    <h2 className="font-semibold">保存报告对照</h2>
    <p className="break-all">左：{leftId ?? "未选择"} · 右：{rightId ?? "未选择"}</p>
    <div className="flex gap-4"><button type="button" disabled={!leftId || !rightId || busy} onClick={compare} className="underline disabled:opacity-40">读取两份保存报告{busy ? "…" : ""}</button><button type="button" onClick={onClear} className="underline">清空选择</button></div>
    <p className="text-[var(--text-muted)]">只读取保存结果，不发起新研究。差值为右减左；参数变化不是因果实验，数据指纹不代表不可变归档或历史时点可获得性认证。</p>
    {error && <p role="alert" className="text-[var(--accent-danger)]">{error}</p>}
    {report && <>
      <p role="status">{report.compatibility.comparable ? "保存方法、流程、数据指纹及请求区间一致，可读取数值差值；不代表策略已验证。" : "不可直接比较：仅并列展示原始保存值，不计算差值。"}</p>
      <p>方法{report.compatibility.same_method ? "一致" : "不同或未记录"} · 数据{report.compatibility.same_data_version ? "一致" : "不同或未记录"} · 流程{report.compatibility.same_workflow ? "一致" : "不同或未记录"} · 区间{report.compatibility.same_requested_period ? "一致" : "不同或未记录"}</p>
      {report.compatibility.warnings.map((warning, index) => <p key={index} className="text-[var(--accent-warning)]">{warning}</p>)}
      <div className="grid gap-3 lg:grid-cols-2">{(["left", "right"] as const).map((side) => <details key={side}><summary>{side === "left" ? "左" : "右"}：{report[side].job.title} · 保存参数与版本</summary><pre className="overflow-auto whitespace-pre-wrap break-all">{JSON.stringify({ method: report[side].job.method_version, workflow: report[side].job.workflow_version, data: report[side].job.data_version, params: report[side].job.params }, null, 2)}</pre></details>)}</div>
      <div className="overflow-x-auto"><table className="w-full text-left"><thead><tr><th>指标</th><th>左报告</th><th>右报告</th><th>差值（右减左）</th></tr></thead><tbody>{report.metrics.map((metric) => <tr key={metric.key}><td className="py-2">{metric.label}</td><td>{valueLabel(metric.left, metric.unit)}</td><td>{valueLabel(metric.right, metric.unit)}</td><td>{valueLabel(report.compatibility.comparable ? metric.delta : null, metric.unit, true)}</td></tr>)}</tbody></table></div>
      <div className="grid gap-3 lg:grid-cols-2">{(["left", "right"] as const).map((side) => <div key={side}><p>{side === "left" ? "左" : "右"}：{report.curves.label}（各自日期，不拼接基准）</p>{report.curves[side].length ? <MiniLineChart points={report.curves[side].map((point) => ({ x: Date.parse(`${point.date}T00:00:00Z`) / 1000, y: point.nav }))} color={side === "left" ? "#f5a623" : "#4f9cf9"} /> : <p>未记录净值曲线</p>}</div>)}</div>
    </>}
  </section>
}
