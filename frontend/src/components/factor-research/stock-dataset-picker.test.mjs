import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"

const requireModule = createRequire(import.meta.url)
const reference = { dataset_id: "74490e6b-b979-4331-87d8-c25b809075dd", data_version: "a".repeat(64), qualification: "observation_only" }
const item = { ...reference, artifact_state: "ready", integrity: "not_checked", created_at: "2026-10-23T05:00:00Z" }
const detail = { reference, manifest_version: "stock-observation-manifest-v1", start: "2026-01-05", end: "2026-01-06", warmup_start: "2026-01-05", pool_id: "本人固定池", pool_version: "b".repeat(64), instruments: ["000001.SZ"], sources: [{ source_id: "5df7727e-dc73-428c-a526-83de24a1fcde", sha256: "c".repeat(64), byte_size: 1024, row_count: 2, kind: "daily" }], integrity: "manifest_verified_sources_not_checked", research_enabled: false, limitations: ["源价格文件未核验"] }
const settle = () => new Promise((resolve) => setImmediate(resolve))

function mount({ enabled = true, fails = false } = {}) {
  const slots = [], effects = [], calls = []
  let cursor = 0
  const props = { token: "token-one", scope: "owner:virtual" }
  const controls = { items: [item], deferred: null, fails }
  const dependencies = {
    react: {
      useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial; return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value }] },
      useRef(initial) { const index = cursor++; if (!(index in slots)) slots[index] = { current: initial }; return slots[index] },
      useEffect(callback, deps) { const index = cursor++; if (!(index in slots) || deps.some((value, position) => value !== slots[index].deps[position])) { slots[index]?.cleanup?.(); slots[index] = { deps }; effects.push(() => { slots[index].cleanup = callback() }) } },
    },
    "@/lib/stock-dataset-api": {
      getStockDatasetAvailability: async (token) => { calls.push({ kind: "availability", token }); if (controls.fails) throw new Error("目录连接失败"); return { catalog_enabled: enabled, detail_enabled: enabled, inspection_enabled: false, research_enabled: false, parquet_enabled: false, reason: "未迁移或完整检查尚未接通" } },
      listStockDatasets: async (query, token, signal) => { calls.push({ kind: "list", query, token, signal }); return { items: controls.items, total: controls.items.length, has_more: false } },
      getStockDatasetDescription: async (fixed, token, signal) => { calls.push({ kind: "detail", fixed, token, signal }); return controls.deferred ?? { ...detail, reference: fixed } },
    },
  }
  const target = new URL("./stock-dataset-picker.tsx", import.meta.url)
  const source = existsSync(target) ? readFileSync(target, "utf8") : "export const StockDatasetPicker = undefined"
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const module = { exports: {} }
  vm.runInNewContext(output, { module, exports: module.exports, AbortController, Error, require: (name) => dependencies[name] ?? requireModule(name) })
  assert.equal(typeof module.exports.StockDatasetPicker, "function", "real read-only snapshot picker exists")
  const render = () => { cursor = 0; const tree = module.exports.StockDatasetPicker(props); effects.splice(0).forEach((effect) => effect()); return tree }
  const ready = async () => { for (let index = 0; index < 4; index++) { render(); await settle() } }
  render()
  return { render, ready, calls, controls, props, unmount: () => slots.forEach((slot) => slot?.cleanup?.()) }
}

function nodes(tree) { if (Array.isArray(tree)) return tree.flatMap(nodes); return tree && typeof tree === "object" ? [tree, ...nodes(tree.props?.children)] : [] }
function text(tree) { if (Array.isArray(tree)) return tree.map(text).join(""); return tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }
function button(page, label) { const found = nodes(page.render()).find((node) => node.type === "button" && text(node).includes(label)); assert.ok(found, label); return found }

test("closed capability shows the reason without fake empty data or compute controls", async () => {
  const page = mount({ enabled: false }); await page.ready()
  assert.match(text(page.render()), /未迁移/)
  assert.equal(page.calls.filter((call) => call.kind === "list").length, 0)
  assert.ok(!text(page.render()).includes("尚无已登记"))
  page.unmount()
})

test("viewing a selected exact version remains read-only and distinguishes manifest from actual coverage", async () => {
  const page = mount(); await page.ready()
  assert.equal(page.calls.filter((call) => call.kind === "detail").length, 0)
  button(page, "查看固定版本").props.onClick(); await page.ready()
  const call = page.calls.find((value) => value.kind === "detail")
  assert.equal(call.fixed.data_version, reference.data_version)
  assert.equal(call.fixed.dataset_id, reference.dataset_id)
  assert.match(text(page.render()), /清单声明区间/)
  assert.match(text(page.render()), /不是实际行情覆盖/)
  assert.match(text(page.render()), /源价格文件未核验/)
  assert.equal(button(page, "用于IC/回测").props.disabled, true)
  assert.ok(page.calls.every((value) => ["availability", "list", "detail"].includes(value.kind)))
  page.unmount()
})

test("refreshing inventory never silently rebinds the inspected reference to a newer version", async () => {
  const page = mount(); await page.ready()
  button(page, "查看固定版本").props.onClick(); await page.ready()
  page.controls.items = [{ ...item, data_version: "f".repeat(64) }]
  button(page, "刷新数据目录").props.onClick(); await page.ready()
  assert.equal(page.calls.filter((call) => call.kind === "detail").length, 1)
  assert.match(text(page.render()), new RegExp(reference.data_version))
  page.unmount()
})

test("scope changes hide old private detail immediately and discard late responses", async () => {
  const page = mount(); await page.ready()
  let complete
  page.controls.deferred = new Promise((resolve) => { complete = resolve })
  button(page, "查看固定版本").props.onClick(); await settle()
  page.props.token = "token-two"; page.props.scope = "other:live"; page.controls.items = []
  assert.ok(!text(page.render()).includes(reference.dataset_id))
  await page.ready(); complete(detail); await page.ready()
  assert.ok(!text(page.render()).includes("本人固定池"))
  assert.match(text(page.render()), /尚无已登记/)
  page.unmount()
})

test("failures are visible and never rendered as a zero dataset count", async () => {
  const page = mount({ fails: true }); await page.ready()
  assert.match(text(page.render()), /目录连接失败/)
  assert.ok(!text(page.render()).includes("尚无已登记"))
  page.unmount()
})
