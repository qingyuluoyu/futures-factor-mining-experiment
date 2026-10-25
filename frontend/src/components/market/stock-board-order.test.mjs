import assert from "node:assert/strict"
import { existsSync } from "node:fs"
import test from "node:test"

async function subject() {
  assert.ok(existsSync(new URL("./stock-board-order.ts", import.meta.url)), "stock board ordering is not implemented")
  return (await import("./stock-board-order.ts")).selectStockBoards
}

const sample = [
  { industry: "银行", count: 42, comparable_count: 40, change_pct: 0 },
  { industry: "煤炭", count: 20, comparable_count: 0, change_pct: null },
  { industry: "半导体", count: 60, comparable_count: 50, change_pct: -2 },
  { industry: "软件", count: 60, comparable_count: 55, change_pct: 3 },
]

for (const direction of ["asc", "desc"]) test(`change ordering ${direction} keeps missing values last and preserves true zero`, async () => {
  const select = await subject()
  const result = select(sample, { sort: "change_pct", direction })
  assert.deepEqual(result.map((item) => item.change_pct), direction === "asc" ? [-2, 0, 3, null] : [3, 0, -2, null])
  assert.equal(result.find((item) => item.industry === "银行").change_pct, 0)
})

test("default count order matches the original backend and does not mutate source", async () => {
  const select = await subject()
  const before = JSON.stringify(sample)
  const result = select(sample, {})
  assert.deepEqual(result.map((item) => item.count), [60, 60, 42, 20])
  assert.equal(result[0].industry, "半导体")
  assert.equal(JSON.stringify(sample), before)
  assert.notEqual(result, sample)
})

test("name direction and keyword are applied only within the supplied boards", async () => {
  const select = await subject()
  const names = select(sample, { sort: "name", direction: "asc" }).map((item) => item.industry)
  assert.deepEqual(select(sample, { sort: "name", direction: "desc" }).map((item) => item.industry), [...names].reverse())
  assert.deepEqual(select(sample, { keyword: " 银行 " }).map((item) => item.industry), ["银行"])
  assert.equal(select(sample, { keyword: "不存在" }).length, 0)
})

test("only comparable filter excludes null but keeps a flat priced sample", async () => {
  const select = await subject()
  assert.deepEqual(select(sample, { comparableOnly: true }).map((item) => item.industry).sort(), ["银行", "半导体", "软件"].sort())
  assert.equal(select([{ ...sample[0], comparable_count: 0 }], { comparableOnly: true }).length, 0)
})

test("default ties preserve the existing backend Unicode name order", async () => {
  const select = await subject()
  const values = [{ ...sample[0], industry: "阿" }, { ...sample[0], industry: "波" }]
  assert.deepEqual(select(values, {}).map((item) => item.industry), ["波", "阿"])
})
