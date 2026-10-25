import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import vm from "node:vm"
import ts from "typescript"

function load() {
  const source = readFileSync(new URL("./paper-valuation.ts", import.meta.url), "utf8")
  const module = { exports: {} }
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, { module, exports: module.exports })
  return module.exports
}

test("legacy placeholder zeros cannot masquerade as calculated floating or daily pnl", () => {
  const { paperValuationView } = load()
  const result = paperValuationView({ total_equity: 10000, unrealized_pnl: 0, today_pnl: 0, risk_rate: 20 })
  assert.match(result.equityLabel, /账面/)
  assert.equal(result.equity, 10000)
  assert.equal(result.floating, null)
  assert.equal(result.today, null)
  assert.equal(result.risk, null)
})

test("unavailable marks keep book equity separate and never coerce null to zero", () => {
  const { paperValuationView } = load()
  const result = paperValuationView({ total_equity: 10000, valuation: { status: "unavailable", book_equity: "10000.00", equity: null, unrealized_pnl: null, risk_rate: null } })
  assert.equal(result.equity, 10000)
  assert.equal(result.floating, null)
  assert.match(result.note, /不可估值/)
})

test("valid cached mark displays loss and negative equity without clipping", () => {
  const { paperValuationView } = load()
  const result = paperValuationView({ total_equity: 10000, valuation: { status: "cache_marked", equity: "-200.25", unrealized_pnl: "-10200.25", today_pnl: null, risk_rate: null } })
  assert.equal(result.equity, -200.25)
  assert.equal(result.floating, -10200.25)
  assert.equal(result.risk, null)
  assert.equal(result.today, null)
  assert.match(result.equityLabel, /缓存参考/)
})
