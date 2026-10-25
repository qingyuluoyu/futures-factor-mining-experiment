import { z } from "zod"
import { researchJobSchema } from "@/lib/stock-research-api"

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "")
const MAX_EXPORT_BYTES = 16 * 1024 * 1024
const MAX_COMPARISON_BYTES = 2 * 1024 * 1024
const formatSchema = z.enum(["json", "csv"])
const numbers = z.record(z.string(), z.number().nullable()).nullable()
const summary = z.object({ job: researchJobSchema, result: z.object({ metadata: z.record(z.string(), z.unknown()), params: z.record(z.string(), z.unknown()), metrics: numbers, ic: numbers, factor_policies: z.array(z.record(z.string(), z.unknown())).nullable().optional() }).passthrough() })
export const savedResearchComparisonSchema = z.object({
  comparison_version: z.literal("stock-saved-comparison-v1"),
  kind: z.enum(["analysis", "backtest"]),
  left: summary,
  right: summary,
  compatibility: z.object({ comparable: z.boolean(), same_method: z.boolean(), same_data_version: z.boolean(), same_workflow: z.boolean(), same_requested_period: z.boolean(), warnings: z.array(z.string()).max(50) }),
  metrics: z.array(z.object({ key: z.string(), label: z.string(), unit: z.enum(["percent", "ratio", "count"]), left: z.number().nullable(), right: z.number().nullable(), delta: z.number().nullable() })).max(32),
  curves: z.object({ label: z.string(), left: z.array(z.object({ date: z.string(), nav: z.number() })).max(10000), right: z.array(z.object({ date: z.string(), nav: z.number() })).max(10000) }),
})
export type SavedResearchComparison = z.infer<typeof savedResearchComparisonSchema>
export type ResearchExportFormat = z.infer<typeof formatSchema>
export interface ResearchReportDownload { blob: Blob; filename: string }

async function reportRequest(path: string, token: string, signal?: AbortSignal): Promise<Response> {
  if (!token) throw new Error("请先登录研究会话")
  const timeout = AbortSignal.timeout(20000)
  const response = await fetch(`${API_BASE}/api/stock/research${path}`, {
    cache: "no-store",
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!response.ok) {
    let message = `报告请求失败: ${response.status}`
    try {
      const payload = JSON.parse(await (await boundedBlob(response, 64 * 1024)).text())
      if (typeof payload?.detail === "string") message = payload.detail.slice(0, 280)
    } catch { message = `报告请求失败: ${response.status}；错误响应无法安全解析` }
    throw Object.assign(new Error(message), { status: response.status })
  }
  return response
}

async function boundedBlob(response: Response, maximum: number): Promise<Blob> {
  const declared = response.headers.get("Content-Length")
  if (declared && (!/^[0-9]+$/.test(declared) || Number(declared) > maximum)) {
    await response.body?.cancel()
    throw new Error("报告文件超过安全大小上限")
  }
  const reader = response.body?.getReader()
  if (!reader) throw new Error("报告响应没有可读取内容")
  const parts: BlobPart[] = []
  let length = 0
  try {
    while (true) {
      const chunk = await reader.read()
      if (chunk.done) break
      length += chunk.value.byteLength
      if (length > maximum) { await reader.cancel(); throw new Error("报告文件超过安全大小上限") }
      const part = new Uint8Array(chunk.value.byteLength)
      part.set(chunk.value)
      parts.push(part.buffer)
    }
  } finally { reader.releaseLock() }
  return new Blob(parts, { type: response.headers.get("Content-Type") ?? "application/octet-stream" })
}

export async function downloadStockResearchReport(id: string, format: ResearchExportFormat, token: string, signal?: AbortSignal): Promise<ResearchReportDownload> {
  const jobId = z.uuid().parse(id)
  const selected = formatSchema.parse(format)
  const response = await reportRequest(`/jobs/${jobId}/export?format=${selected}`, token, signal)
  const contentType = response.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase()
  if (contentType !== (selected === "json" ? "application/json" : "text/csv")) throw new Error("报告文件类型与请求不一致，未下载")
  return { blob: await boundedBlob(response, MAX_EXPORT_BYTES), filename: `stock-research-${jobId}.${selected}` }
}

export async function compareStockResearchReports(left: string, right: string, token: string, signal?: AbortSignal): Promise<SavedResearchComparison> {
  const leftId = z.uuid().parse(left)
  const rightId = z.uuid().parse(right)
  if (leftId === rightId) throw new Error("请选择两份不同的报告")
  const response = await reportRequest(`/compare?left_id=${leftId}&right_id=${rightId}`, token, signal)
  const result = savedResearchComparisonSchema.parse(JSON.parse(await (await boundedBlob(response, MAX_COMPARISON_BYTES)).text()))
  if (result.left.job.id !== leftId || result.right.job.id !== rightId) throw new Error("对照结果与选择的研究任务不匹配")
  return result
}

export function startResearchReportDownload(download: ResearchReportDownload): void {
  const address = URL.createObjectURL(download.blob)
  const anchor = document.createElement("a")
  try {
    anchor.href = address
    anchor.download = download.filename
    anchor.style.display = "none"
    document.body.appendChild(anchor)
    anchor.click()
  } finally {
    anchor.remove()
    setTimeout(() => URL.revokeObjectURL(address), 0)
  }
}
