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

function plain(value) { return JSON.parse(JSON.stringify(value)) }

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

test("re-recording the same idempotency key replaces the slot instead of duplicating the write", () => {
  const page = mount()
  page.recordPendingWrite("owner:virtual", entry())
  page.recordPendingWrite("owner:virtual", entry({ signature: JSON.stringify(["order:account-a", { code: "000002" }]), created_at: "2026-10-26T03:00:00.000Z" }))
  const rows = page.readPendingWrites("owner:virtual")
  assert.equal(rows.length, 1)
  assert.equal(rows[0].created_at, "2026-10-26T03:00:00.000Z")
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

test("a registry that cannot grow without bound drops the oldest unconfirmed writes", () => {
  const page = mount()
  for (let index = 0; index < 14; index += 1) {
    page.recordPendingWrite("owner:virtual", entry({ idempotency_key: `key-${index}`, created_at: `2026-10-26T00:${String(index).padStart(2, "0")}:00.000Z` }))
  }
  const rows = page.readPendingWrites("owner:virtual")
  assert.equal(rows.length, 8)
  assert.equal(rows.at(-1).idempotency_key, "key-13")
  assert.equal(rows[0].idempotency_key, "key-6")
})

test("unavailable or hostile storage never throws and never blocks the write itself", () => {
  const hostile = {
    getItem() { throw new Error("blocked") },
    setItem() { throw new Error("quota exceeded") },
    removeItem() { throw new Error("blocked") },
  }
  const page = { ...loadPendingWrites(hostile) }
  assert.deepEqual(plain(page.readPendingWrites("owner:virtual")), [])
  page.recordPendingWrite("owner:virtual", entry())
  page.clearPendingWrite("owner:virtual", entry().idempotency_key)
  const missing = loadPendingWrites(undefined)
  assert.deepEqual(plain(missing.readPendingWrites("owner:virtual")), [])
  missing.recordPendingWrite("owner:virtual", entry())
  missing.clearPendingWrite("owner:virtual", entry().idempotency_key)
})

test("a client minted handle exists for writes that carry no server idempotency key", () => {
  const page = mount()
  const first = page.newPendingWriteKey()
  const second = page.newPendingWriteKey()
  assert.match(first, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/)
  assert.notEqual(first, second)
})
