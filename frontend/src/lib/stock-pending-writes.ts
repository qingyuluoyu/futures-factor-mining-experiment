/**
 * 股票独占的「跨页未确认写」注册表。
 *
 * 只登记**结果未知**的写入：请求已经发出，但既没有拿到成功回执、也没有拿到确定性失败。
 * 键为 `${userId}:${tradingMode}`（与 `stockResearchScope` 同一形态），值为该用户该登录盘
 * 的全部未确认写条目，因此导航到任何股票页面后仍可被读到并以**原幂等键**重试。
 *
 * 硬约束：
 * - 与 `use-research-task.ts` 的持久幂等协议彼此独立；本文件不得改动那个 hook，也不复用它的存储键。
 * - 只保存 `kind` / `idempotency_key` / `expected_revision` / `signature` / `created_at`。
 *   任何额外字段（尤其是 access token）在写入前被丢弃，绝不落盘。
 * - 所有 `localStorage` 访问都包在 try/catch 内：隐私模式、配额耗尽或存储不可用时读返回空、
 *   写静默失败，注册表永远不能中断真实写入链路。
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

const STORAGE_PREFIX = "stock-pending-writes:"
const MAX_ENTRIES = 8
const MAX_SERIALIZED = 16384
const MAX_FIELD = 8192

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
  return {
    kind,
    idempotency_key: idempotencyKey,
    ...(typeof revision === "number" && Number.isInteger(revision) && revision >= 0 ? { expected_revision: revision } : {}),
    signature,
    created_at: createdAt,
  }
}

function decode(raw: string | null): PendingWriteEntry[] {
  if (!raw) return []
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return []
  }
  if (!Array.isArray(parsed)) return []
  return parsed.map(normalize).filter((item): item is PendingWriteEntry => item !== null)
}

export function readPendingWrites(scope: string): PendingWriteEntry[] {
  const storage = browserStorage()
  if (!storage) return []
  try {
    return decode(storage.getItem(pendingWriteStorageKey(scope)))
  } catch {
    return []
  }
}

export function recordPendingWrite(scope: string, entry: PendingWriteEntry): void {
  const normalized = normalize(entry)
  if (!normalized) return
  const storage = browserStorage()
  if (!storage) return
  const kept = [...readPendingWrites(scope).filter((item) => item.idempotency_key !== normalized.idempotency_key), normalized].slice(-MAX_ENTRIES)
  let serialized = JSON.stringify(kept)
  if (serialized.length > MAX_SERIALIZED) serialized = JSON.stringify([normalized])
  try {
    storage.setItem(pendingWriteStorageKey(scope), serialized)
  } catch {}
}

export function clearPendingWrite(scope: string, idempotencyKey: string): void {
  const storage = browserStorage()
  if (!storage) return
  const kept = readPendingWrites(scope).filter((item) => item.idempotency_key !== idempotencyKey)
  try {
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
