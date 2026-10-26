import assert from "node:assert/strict"
import test from "node:test"
import { loadPendingWrites, memoryStorage } from "./stock-pending-writes-harness.mjs"

const entry = (overrides = {}) => ({
  kind: "replay-order",
  idempotency_key: "3f6b1f6e-5b8d-4a4e-9f6a-1f2c3d4e5f60",
  expected_revision: 7,
  signature: JSON.stringify(["order:account-a", { code: "000001", direction: "buy", quantity: 100 }]),
  created_at: "2026-10-26T02:00:00.000Z",
  ...overrides,
})

function mount(initial) {
  const storage = memoryStorage(initial)
  return { storage, ...loadPendingWrites(storage, { crypto }) }
}

function plain(value) { return value === undefined ? undefined : JSON.parse(JSON.stringify(value)) }

test("an untouched registry reads empty and writes nothing until a write is unconfirmed", () => {
  const page = mount()
  assert.deepEqual(plain(page.readPendingWrites("owner:virtual")), [])
  assert.equal(page.storage.map.size, 0)
})

test("recording an unconfirmed write round-trips every registry field", () => {
  const page = mount()
  page.recordPendingWrite("owner:virtual", entry())
  assert.deepEqual(plain(page.readPendingWrites("owner:virtual")), [entry()])
  assert.equal(page.pendingWriteStorageKey("owner:virtual"), "stock-pending-writes:owner:virtual")
})

test("same-key retries preserve the original intent and timestamp while conflicting payloads are rejected", () => {
  const page = mount()
  assert.equal(page.recordPendingWrite("owner:virtual", entry())?.ok, true)
  assert.equal(page.recordPendingWrite("owner:virtual", entry({ created_at: "2026-10-26T03:00:00.000Z" }))?.ok, true)
  assert.deepEqual(plain(page.readPendingWrites("owner:virtual")), [entry()])
  for (const conflict of [{ signature: JSON.stringify(["order:account-a", { code: "000002" }]) }, { kind: "replay-step" }, { expected_revision: 8 }]) {
    assert.deepEqual(plain(page.recordPendingWrite("owner:virtual", entry(conflict))), { ok: false, reason: "key_conflict" })
    assert.deepEqual(plain(page.readPendingWrites("owner:virtual")), [entry()])
  }
})

test("clearing removes only the requested key and never touches another user or trading mode", () => {
  const page = mount()
  page.recordPendingWrite("owner:virtual", entry())
  page.recordPendingWrite("owner:virtual", entry({ kind: "custom-factor-save", idempotency_key: "8d4b7d1e-4f2a-4c33-9a11-7c6b5a4d3e2f" }))
  page.recordPendingWrite("owner:live", entry({ kind: "replay-step" }))
  page.clearPendingWrite("owner:virtual", entry().idempotency_key)
  const rows = page.readPendingWrites("owner:virtual")
  assert.deepEqual(plain(rows.map((item) => item.kind)), ["custom-factor-save"])
  assert.equal(page.readPendingWrites("owner:live").length, 1)
  page.clearPendingWrite("owner:virtual", "8d4b7d1e-4f2a-4c33-9a11-7c6b5a4d3e2f")
  assert.deepEqual(plain(page.readPendingWrites("owner:virtual")), [])
  assert.equal(page.storage.getItem("stock-pending-writes:owner:virtual"), null)
})

test("corrupt, foreign or oversized stored payloads never surface as pending writes", () => {
  const page = mount({ "stock-pending-writes:owner:virtual": "not json at all" })
  assert.deepEqual(plain(page.readPendingWrites("owner:virtual")), [])
  page.storage.map.set("stock-pending-writes:owner:virtual", JSON.stringify({ kind: "replay-order" }))
  assert.deepEqual(plain(page.readPendingWrites("owner:virtual")), [])
  page.storage.map.set("stock-pending-writes:owner:virtual", JSON.stringify([entry({ idempotency_key: "" }), null, 5, entry({ kind: 7 })]))
  assert.deepEqual(plain(page.readPendingWrites("owner:virtual")), [])
  page.storage.map.set("stock-pending-writes:owner:virtual", JSON.stringify("replay-order"))
  assert.deepEqual(plain(page.readPendingWrites("owner:virtual")), [])
})

test("entries without expected revision are kept and revisions stay integral", () => {
  const page = mount()
  const { expected_revision: _ignored, ...withoutRevision } = entry()
  page.recordPendingWrite("owner:virtual", withoutRevision)
  assert.deepEqual(plain(page.readPendingWrites("owner:virtual")), [withoutRevision])
  page.recordPendingWrite("owner:virtual", entry({ expected_revision: 1.5 }))
  assert.equal(page.readPendingWrites("owner:virtual")[0].expected_revision, undefined)
  page.recordPendingWrite("owner:virtual", entry({ expected_revision: -3 }))
  assert.equal(page.readPendingWrites("owner:virtual")[0].expected_revision, undefined)
})

test("no token or extra field is ever persisted, even when a caller passes one", () => {
  const page = mount()
  page.recordPendingWrite("owner:virtual", entry({ token: "header.payload.signature", body: { quantity: 100 } }))
  const raw = page.storage.getItem("stock-pending-writes:owner:virtual")
  assert.doesNotMatch(raw, /token|header\.payload|body/)
  assert.deepEqual(plain(page.readPendingWrites("owner:virtual")), [entry()])
})

test("a full registry rejects new pending writes without evicting any unresolved request and permits an exact retry", () => {
  const page = mount()
  const original = []
  for (let index = 0; index < 8; index += 1) {
    const item = entry({ idempotency_key: `key-${index}` }); original.push(item)
    assert.equal(page.recordPendingWrite("owner:virtual", item)?.ok, true)
  }
  const before = page.storage.getItem(page.pendingWriteStorageKey("owner:virtual"))
  assert.deepEqual(plain(page.recordPendingWrite("owner:virtual", entry({ idempotency_key: "key-8" }))), { ok: false, reason: "registry_full" })
  assert.equal(page.storage.getItem(page.pendingWriteStorageKey("owner:virtual")), before)
  assert.deepEqual(plain(page.readPendingWrites("owner:virtual")), original)
  assert.equal(page.recordPendingWrite("owner:virtual", { ...original[0], created_at: "2026-10-26T03:00:00.000Z" }).ok, true)
  assert.equal(page.storage.getItem(page.pendingWriteStorageKey("owner:virtual")), before)
})

test("unavailable or hostile storage never throws and returns an explicit refusal before financial submission", () => {
  const hostile = {
    getItem() { throw new Error("blocked") },
    setItem() { throw new Error("quota exceeded") },
    removeItem() { throw new Error("blocked") },
  }
  const page = { ...loadPendingWrites(hostile) }
  assert.deepEqual(plain(page.readPendingWrites("owner:virtual")), [])
  assert.deepEqual(plain(page.recordPendingWrite("owner:virtual", entry())), { ok: false, reason: "storage_read_failed" })
  page.clearPendingWrite("owner:virtual", entry().idempotency_key)
  const missing = loadPendingWrites(undefined)
  assert.deepEqual(plain(missing.readPendingWrites("owner:virtual")), [])
  assert.deepEqual(plain(missing.recordPendingWrite("owner:virtual", entry())), { ok: false, reason: "storage_unavailable" })
  missing.clearPendingWrite("owner:virtual", entry().idempotency_key)
})

test("a client minted handle exists for writes that carry no server idempotency key", () => {
  const page = mount()
  const first = page.newPendingWriteKey()
  const second = page.newPendingWriteKey()
  assert.match(first, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/)
  assert.notEqual(first, second)
})

test("serialized size refuses a new record without replacing the previously recoverable request", () => {
  const page = mount()
  const first = entry({ signature: "x".repeat(8100) })
  assert.equal(page.recordPendingWrite("owner:virtual", first)?.ok, true)
  const before = page.storage.getItem(page.pendingWriteStorageKey("owner:virtual"))
  assert.deepEqual(plain(page.recordPendingWrite("owner:virtual", entry({ idempotency_key: "other-key", signature: "y".repeat(8100) }))), { ok: false, reason: "serialized_limit" })
  assert.equal(page.storage.getItem(page.pendingWriteStorageKey("owner:virtual")), before)
  assert.deepEqual(plain(page.readPendingWrites("owner:virtual")), [first])
})

test("corrupt or oversized existing registries are never silently overwritten or cleared", () => {
  for (const raw of ["not-json", "x".repeat(20000), JSON.stringify([entry(), { kind: "broken" }]), JSON.stringify([entry(), entry()])]) {
    const page = mount({ "stock-pending-writes:owner:virtual": raw })
    assert.deepEqual(plain(page.recordPendingWrite("owner:virtual", entry({ idempotency_key: "new-key" }))), { ok: false, reason: "corrupt_registry" })
    page.clearPendingWrite("owner:virtual", entry().idempotency_key)
    assert.equal(page.storage.getItem("stock-pending-writes:owner:virtual"), raw)
  }
})

test("the serialized resource boundary counts UTF-8 bytes and never erases an older oversized non-ASCII registry", () => {
  const page = mount()
  assert.equal(page.recordPendingWrite("owner:virtual", entry())?.ok, true)
  const key = page.pendingWriteStorageKey("owner:virtual"), before = page.storage.getItem(key)
  const oversized = entry({ idempotency_key: "chinese-key", signature: "中".repeat(6000) })
  assert.deepEqual(plain(page.recordPendingWrite("owner:virtual", oversized)), { ok: false, reason: "serialized_limit" })
  assert.equal(page.storage.getItem(key), before)
  const oldRaw = JSON.stringify([oversized])
  assert.ok(oldRaw.length < 16384 && Buffer.byteLength(oldRaw, "utf8") > 16384)
  page.storage.setItem(key, oldRaw)
  assert.deepEqual(plain(page.recordPendingWrite("owner:virtual", entry())), { ok: false, reason: "corrupt_registry" })
  page.clearPendingWrite("owner:virtual", oversized.idempotency_key)
  assert.equal(page.storage.getItem(key), oldRaw)
  assert.deepEqual(plain(page.readPendingWrites("owner:virtual")), [])
})

test("failed or unverified persistence cannot produce a successful reservation receipt", () => {
  const failing = memoryStorage()
  failing.setItem = () => { throw new Error("private device details") }
  const rejected = loadPendingWrites(failing)
  assert.deepEqual(plain(rejected.recordPendingWrite("owner:virtual", entry())), { ok: false, reason: "storage_write_failed" })
  const discarded = memoryStorage(); discarded.setItem = () => {}
  assert.deepEqual(plain(loadPendingWrites(discarded).recordPendingWrite("owner:virtual", entry())), { ok: false, reason: "write_not_verified" })
})

test("invalid registration input is refused and failure messages never reveal an intent or internal storage error", () => {
  const page = mount()
  for (const item of [entry({ kind: "" }), entry({ expected_revision: 1.5 }), entry({ expected_revision: -1 }), entry({ signature: "x".repeat(8193) })]) {
    assert.deepEqual(plain(page.recordPendingWrite("owner:virtual", item)), { ok: false, reason: "invalid_entry" })
  }
  assert.equal(page.storage.map.size, 0)
  for (const reason of ["invalid_entry", "storage_unavailable", "storage_read_failed", "corrupt_registry", "registry_full", "serialized_limit", "storage_write_failed", "write_not_verified", "key_conflict"]) {
    const message = page.pendingWriteFailureMessage(reason)
    assert.equal(typeof message, "string"); assert.ok(message.length > 5)
    assert.doesNotMatch(message, /private device|account-a|000001|accessToken|signature/)
    // Some callers attempt registration after an uncertain response. The registry cannot
    // know whether a prior HTTP request was sent; only the pre-submit caller can say so.
    assert.doesNotMatch(message, /尚未发送|未请求后端|没有发送/)
  }
})
