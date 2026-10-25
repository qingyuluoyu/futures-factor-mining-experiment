import assert from "node:assert/strict"
import test from "node:test"
import { beginSessionRestoreState, loadSessionOnce, sessionStillCurrent } from "./auth-session.ts"

test("strict effect replay shares a pending authentication lookup", async () => {
  let calls = 0
  let resolve
  const loader = () => { calls += 1; return new Promise((done) => { resolve = done }) }
  const first = loadSessionOnce("qa-one", loader)
  const second = loadSessionOnce("qa-one", loader)
  assert.equal(calls, 1)
  resolve({ id: "qa-one" })
  assert.deepEqual(await first, await second)
})

test("different tokens never share user results and failed requests can retry", async () => {
  assert.deepEqual(await Promise.all([loadSessionOnce("qa-a", async () => "a"), loadSessionOnce("qa-b", async () => "b")]), ["a", "b"])
  await assert.rejects(loadSessionOnce("qa-fail", async () => { throw new Error("invalid") }))
  assert.equal(await loadSessionOnce("qa-fail", async () => "recovered"), "recovered")
})

test("a stale success or failure cannot write to another login session", async () => {
  for (const fails of [false, true]) {
    let complete
    let storageToken = "qa-old"
    let storeToken = "qa-old"
    let writes = 0
    const pending = loadSessionOnce(`qa-stale-${fails}`, () => new Promise((resolve, reject) => { complete = () => fails ? reject(new Error("old failed")) : resolve("old user") }))
    const finish = pending.then(() => { if (sessionStillCurrent("qa-old", storeToken, storageToken)) writes += 1 }, () => { if (sessionStillCurrent("qa-old", storeToken, storageToken)) writes += 1 })
    storageToken = "qa-new"
    storeToken = "qa-new"
    complete()
    await finish
    assert.equal(writes, 0)
  }
})

test("an external replacement invalidates loaded user before restoring another token", () => {
  const previous = { user: { id: "qa-a" }, accessToken: "qa-a", loaded: true, hydrating: true }
  const restoring = { ...previous, ...beginSessionRestoreState("qa-b") }
  assert.equal(restoring.user, null)
  assert.equal(restoring.accessToken, "qa-b")
  assert.equal(restoring.loaded, false)
  assert.equal(restoring.hydrating, false)
})
