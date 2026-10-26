/**
 * 股票独占的「跨页未确认写」注册表。
 *
 * 金融写入先预登记，取得成功回执后才能发送；成功或确定性拒绝后移除。
 * 键为 `${userId}:${tradingMode}`（与 `stockResearchScope` 同一形态），值为该用户该登录盘
 * 的全部未确认写条目，因此导航到任何股票页面后仍可被读到并以**原幂等键**重试。
 *
 * 硬约束：
 * - 与 `use-research-task.ts` 的持久幂等协议彼此独立；本文件不得改动那个 hook，也不复用它的存储键。
 * - 只保存 `kind` / `idempotency_key` / `expected_revision` / `signature` / `created_at`。
 *   任何额外字段（尤其是 access token）在写入前被丢弃，绝不落盘。
 * - 所有 `localStorage` 访问都包在 try/catch 内。旧读取接口保留空数组降级，
 *   写入必须检查显式回执；失败时不能发送新的金融请求，也不驱逐已有未确认记录。
 * - **卸载不清除**。条目只能由「明确成功」「确定性 4xx 失败」或用户显式放弃来移除；
 *   组件卸载时原样保留，这正是跨页恢复的前提。
 */

export type PendingWriteEntry = {
  kind: string
  idempotency_key: string
  expected_revision?: number
  signature: string
  created_at: string
}

export type PendingWriteFailure = "invalid_entry" | "storage_unavailable" | "storage_read_failed"
  | "corrupt_registry" | "registry_full" | "serialized_limit" | "storage_write_failed"
  | "write_not_verified" | "key_conflict"
export type PendingWriteReceipt = { ok: true } | { ok: false; reason: PendingWriteFailure }

/** Describes persistence only; callers know whether their HTTP request has been sent. */
export function pendingWriteFailureMessage(reason: PendingWriteFailure): string {
  const messages: Record<PendingWriteFailure, string> = {
    invalid_entry: "请求恢复信息无效，无法登记。请核对原请求。",
    storage_unavailable: "浏览器恢复存储不可用，无法登记。请允许本地存储后核对原请求。",
    storage_read_failed: "无法读取未确认请求，无法登记。恢复本地存储后请核对原请求。",
    corrupt_registry: "已有请求恢复记录异常，无法登记。请先核对原未确认请求，未自动删除记录。",
    registry_full: "未确认请求已占满恢复空间，无法登记。请先确认或解决已有请求。",
    serialized_limit: "请求恢复信息超过存储大小，无法登记。已有未确认记录保留。",
    storage_write_failed: "无法保存请求恢复信息，无法登记。恢复存储后请核对原请求。",
    write_not_verified: "请求恢复信息未能读回确认，未能登记。请核对存储和原请求。",
    key_conflict: "原请求标识对应不同内容，无法登记。请核对原未确认请求，未覆盖其记录。",
  }
  return messages[reason]
}

const STORAGE_PREFIX = "stock-pending-writes:"
const MAX_ENTRIES = 8
const MAX_SERIALIZED = 16384 // UTF-8 bytes, independent of the browser's storage accounting.
const MAX_FIELD = 8192

function exceedsSerializedLimit(value: string): boolean {
  if (value.length > MAX_SERIALIZED) return true
  let bytes = 0
  for (const character of value) {
    const point = character.codePointAt(0)!
    bytes += point <= 0x7f ? 1 : point <= 0x7ff ? 2 : point <= 0xffff ? 3 : 4
    if (bytes > MAX_SERIALIZED) return true
  }
  return false
}

export function pendingWriteStorageKey(scope: string): string {
  return `${STORAGE_PREFIX}${scope}`
}

function browserStorage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage
  } catch {
    return null
  }
}

function field(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 && value.length <= MAX_FIELD ? value : null
}

function normalize(value: unknown): PendingWriteEntry | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null
  const candidate = value as Record<string, unknown>
  const kind = field(candidate.kind)
  const idempotencyKey = field(candidate.idempotency_key)
  const signature = field(candidate.signature)
  const createdAt = field(candidate.created_at)
  if (!kind || !idempotencyKey || !signature || !createdAt) return null
  const revision = candidate.expected_revision
  if (revision !== undefined && (typeof revision !== "number" || !Number.isSafeInteger(revision) || revision < 0)) return null
  return {
    kind,
    idempotency_key: idempotencyKey,
    ...(typeof revision === "number" && Number.isInteger(revision) && revision >= 0 ? { expected_revision: revision } : {}),
    signature,
    created_at: createdAt,
  }
}

type RegistrySnapshot = { ok: true; entries: PendingWriteEntry[] } | { ok: false; reason: PendingWriteFailure }

function decode(raw: string | null): RegistrySnapshot {
  if (raw === null) return { ok: true, entries: [] }
  if (!raw || exceedsSerializedLimit(raw)) return { ok: false, reason: "corrupt_registry" }
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return { ok: false, reason: "corrupt_registry" }
  }
  if (!Array.isArray(parsed) || parsed.length > MAX_ENTRIES) return { ok: false, reason: "corrupt_registry" }
  const entries = parsed.map(normalize)
  if (entries.some((item) => item === null)) return { ok: false, reason: "corrupt_registry" }
  const validated = entries as PendingWriteEntry[]
  if (new Set(validated.map((item) => item.idempotency_key)).size !== validated.length) return { ok: false, reason: "corrupt_registry" }
  return { ok: true, entries: validated }
}

export function readPendingWrites(scope: string): PendingWriteEntry[] {
  const storage = browserStorage()
  if (!storage) return []
  try {
    const snapshot = decode(storage.getItem(pendingWriteStorageKey(scope)))
    return snapshot.ok ? snapshot.entries : []
  } catch {
    return []
  }
}

export function recordPendingWrite(scope: string, entry: PendingWriteEntry): PendingWriteReceipt {
  const normalized = normalize(entry)
  if (!normalized || !scope || scope.length > 512) return { ok: false, reason: "invalid_entry" }
  const storage = browserStorage()
  if (!storage) return { ok: false, reason: "storage_unavailable" }
  const key = pendingWriteStorageKey(scope)
  let snapshot: RegistrySnapshot
  try { snapshot = decode(storage.getItem(key)) }
  catch { return { ok: false, reason: "storage_read_failed" } }
  if (!snapshot.ok) return snapshot
  const existing = snapshot.entries.find((item) => item.idempotency_key === normalized.idempotency_key)
  if (existing) {
    const sameIntent = existing.kind === normalized.kind && existing.signature === normalized.signature
      && existing.expected_revision === normalized.expected_revision
    return sameIntent ? { ok: true } : { ok: false, reason: "key_conflict" }
  }
  if (snapshot.entries.length >= MAX_ENTRIES) return { ok: false, reason: "registry_full" }
  const serialized = JSON.stringify([...snapshot.entries, normalized])
  if (exceedsSerializedLimit(serialized)) return { ok: false, reason: "serialized_limit" }
  try {
    storage.setItem(key, serialized)
  } catch { return { ok: false, reason: "storage_write_failed" } }
  try {
    if (storage.getItem(key) !== serialized) return { ok: false, reason: "write_not_verified" }
  } catch { return { ok: false, reason: "write_not_verified" } }
  return { ok: true }
}

export function clearPendingWrite(scope: string, idempotencyKey: string): void {
  const storage = browserStorage()
  if (!storage) return
  try {
    const snapshot = decode(storage.getItem(pendingWriteStorageKey(scope)))
    if (!snapshot.ok) return
    const kept = snapshot.entries.filter((item) => item.idempotency_key !== idempotencyKey)
    if (kept.length === snapshot.entries.length) return
    if (kept.length) storage.setItem(pendingWriteStorageKey(scope), JSON.stringify(kept))
    else storage.removeItem(pendingWriteStorageKey(scope))
  } catch {}
}

/** 仅用于服务端本身没有幂等键的写入（如报告收藏）：生成本地句柄，供登记与清除配对。 */
export function newPendingWriteKey(): string {
  try {
    return crypto.randomUUID()
  } catch {
    return `pending-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
  }
}
