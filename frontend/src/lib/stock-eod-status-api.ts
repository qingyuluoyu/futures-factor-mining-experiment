import { z } from "zod"
import { request } from "./api.ts"

const count = z.number().int().nonnegative()
const timestamp = z.iso.datetime({ offset: true })
const archiveStatus = z.object({
  status_version: z.literal("stock-archive-status-v1"),
  enabled: z.boolean(),
  ready: z.boolean(),
  worker_running: z.boolean(),
  state: z.enum(["disabled", "unavailable", "stopped", "idle", "running", "completed", "failed", "cancelled", "expired"]),
  message: z.string().min(1),
  latest_job: z.object({
    id: z.uuid(), request_id: z.string().regex(/^[a-f0-9]{32}$/), source_batch_id: z.uuid(),
    source_data_version: z.string().regex(/^[a-f0-9]{64}$/), trade_date: z.iso.date(),
    status: z.enum(["running", "completed", "failed", "cancelled"]),
    step: z.enum(["archiving", "completed", "failed", "cancelled"]), attempts: z.literal(1),
    started_at: timestamp, updated_at: timestamp, deadline_at: timestamp, finished_at: timestamp.nullable(),
    archive_batch_id: z.uuid().nullable(), error_code: z.string().min(1).max(64).nullable(),
  }).refine((job) => {
    if (job.status === "running") return job.step === "archiving" && job.finished_at === null && job.archive_batch_id === null && job.error_code === null
    if (job.status === "completed") return job.step === "completed" && job.finished_at !== null && job.archive_batch_id !== null && job.error_code === null
    return job.step === job.status && job.finished_at !== null && job.archive_batch_id === null && job.error_code !== null
  }).nullable(),
  scope: z.literal("shadow_only_user_read_path_unchanged"),
  source_calls: z.literal(0),
  model_calls: z.literal(0),
})
const catchupStatus = z.object({
  policy_version: z.literal("stock-eod-catchup-v1"), window_start: z.iso.date(), window_end: z.iso.date(),
  pending_dates: z.array(z.iso.date()).max(7), failed_dates: z.array(z.iso.date()).max(7), expired_dates: z.array(z.iso.date()).max(7),
  calendar_verified: z.literal(false), max_dates_per_cycle: z.literal(1), max_source_calls_per_date: z.literal(3), message: z.string().min(1),
  automatic_enabled: z.boolean().optional(),
}).strict().refine((value) => {
  const dates = [...value.pending_dates, ...value.failed_dates, ...value.expired_dates]
  return Date.parse(value.window_end) - Date.parse(value.window_start) === 6 * 86400000 && dates.length <= 7 && new Set(dates).size === dates.length && dates.every((day) => day >= value.window_start && day <= value.window_end)
}, "漏日窗口或日期范围无效")
const status = z.object({
  status_version: z.literal("stock-eod-status-v1"),
  server_time: timestamp,
  timezone: z.literal("Asia/Shanghai"),
  collection_window: z.literal("16:30"),
  schedule_interval_seconds: z.number().int().positive(),
  enabled: z.boolean(),
  sync_enabled: z.boolean(),
  worker_running: z.boolean(),
  state: z.enum(["disabled", "unavailable", "manual_only", "stopped", "before_window", "pending", "running", "closed", "completed", "partially_completed", "failed", "expired"]),
  message: z.string().min(1),
  today_job: z.object({
    id: z.string().min(1), trade_date: z.iso.date(), status: z.string().min(1), step: z.string(), attempts: count,
    started_at: timestamp.nullable(), deadline_at: timestamp.nullable(), finished_at: timestamp.nullable(), error_code: z.string().nullable(),
  }).nullable(),
  latest_snapshot: z.object({
    trade_date: z.iso.date(), data_version: z.string().min(1), collected_at: timestamp.nullable(),
    provider: z.string().min(1), price_adjustment: z.string().min(1),
    coverage: z.object({ expected: count, quoted: count, missing: count, unknown: count }),
    raw_response_evidence: z.boolean(),
    recovery_applied: z.boolean().optional(),
  }).nullable(),
  verification: z.literal("stored_metadata_only_not_independent_certification"),
  archive: archiveStatus.optional(),
  catchup: catchupStatus.nullable().optional(),
})

export type StockEodStatusResponse = z.infer<typeof status>

export async function getStockEodStatusApi(signal?: AbortSignal): Promise<StockEodStatusResponse> {
  const timeout = AbortSignal.timeout(10000)
  const result = status.safeParse(await request<unknown>("/api/stock/market/sync-status", {
    method: "GET", cache: "no-store", signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  }))
  if (!result.success) throw new Error("同步状态响应格式异常，请重试或联系管理员；未将异常当作无数据")
  return result.data
}
