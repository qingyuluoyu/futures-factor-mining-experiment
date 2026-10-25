import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import vm from "node:vm"
import ts from "typescript"

const settle = () => new Promise((resolve) => setImmediate(resolve))
function fixture() {
  const slots = [], effects = [], requests = []
  let cursor = 0, dependencies = []
  const react = {
    useState(initial) { const position = cursor++; if (!(position in slots)) slots[position] = initial; return [slots[position], (value) => { slots[position] = typeof value === "function" ? value(slots[position]) : value }] },
    useCallback(callback) { return callback },
    useEffect(callback, deps) { if (deps.some((value, index) => value !== dependencies[index])) { effects.push(callback); dependencies = deps } },
  }
  const api = { getChampions: (id) => new Promise((resolve, reject) => requests.push({ id, resolve, reject })) }
  const module = { exports: {} }
  const source = readFileSync(new URL("./use-mining-tasks.ts", import.meta.url), "utf8")
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, { module, exports: module.exports, Error, require: (name) => name === "react" ? react : api })
  let cleanup
  return { requests, render(id) { cursor = 0; const state = module.exports.useTaskChampions(id); for (const callback of effects.splice(0)) { cleanup?.(); cleanup = callback() } return state } }
}

test("champion read failure is explicit and user retry can recover", async () => {
  const hook = fixture(); hook.render("a")
  hook.requests[0].reject(new Error("接口超时")); await settle()
  const failed = hook.render("a")
  assert.equal(failed.error, "接口超时")
  assert.equal(failed.loading, false)
  failed.retry(); hook.render("a")
  hook.requests[1].resolve([]); await settle()
  const empty = hook.render("a")
  assert.equal(empty.error, null)
  assert.equal(empty.champions.length, 0)
  assert.equal(empty.loading, false)
})

test("switching task never shows previous results or accepts late responses", async () => {
  const hook = fixture(); hook.render("a")
  hook.requests[0].resolve([{ text: "a-result" }]); await settle()
  assert.equal(hook.render("a").champions[0].text, "a-result")
  const switching = hook.render("b")
  assert.equal(switching.champions.length, 0)
  assert.equal(switching.loading, true)
  hook.render("c")
  hook.requests[1].resolve([{ text: "stale-b" }]); await settle()
  assert.equal(hook.render("c").champions.length, 0)
  hook.requests[2].resolve([{ text: "c-result" }]); await settle()
  assert.equal(hook.render("c").champions[0].text, "c-result")
})

test("mining screen does not claim full history or verified resume before those capabilities exist", () => {
  const source = readFileSync(new URL("./super-factor-page.tsx", import.meta.url), "utf8")
  assert.doesNotMatch(source, /2000 年至今全量历史|支持暂停\/恢复\/断点续训/)
  assert.match(source, /固定输入快照/)
  assert.match(source, /仍在修复/)
  assert.match(source, /supported\?\.accepting !== true/)
  assert.match(source, /生产任务执行器尚未接通检查点/)
})
