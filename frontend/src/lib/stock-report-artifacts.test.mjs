import assert from "node:assert/strict"
import { existsSync } from "node:fs"
import test from "node:test"

const api = existsSync(new URL("./stock-report-artifacts.ts", import.meta.url)) ? await import("./stock-report-artifacts.ts") : {}
const first = "11111111-1111-4111-8111-111111111111"
const second = "22222222-2222-4222-8222-222222222222"

function fixture(id) {
  return { id, kind: "backtest", title: "真实保存报告", status: "completed", step: "completed", progress: 100, request_id: "qa", attempts: 1, created_at: "2026-10-19", updated_at: "2026-10-19", started_at: "2026-10-19", finished_at: "2026-10-19", method_version: "v4", workflow_version: "v1", data_version: "a".repeat(64), has_result: true, error: null, params: {} }
}

test("export requests only the captured bearer and ignores untrusted filename headers", async () => {
  assert.equal(typeof api.downloadStockResearchReport, "function")
  globalThis.fetch = async (url, options) => {
    assert.ok(url.endsWith(`/jobs/${first}/export?format=json`))
    assert.equal(options.headers.Authorization, "Bearer captured-token")
    return new Response('{"saved":true}', { headers: { "Content-Type": "application/json", "Content-Disposition": "attachment; filename=../../unsafe.html" } })
  }
  const result = await api.downloadStockResearchReport(first, "json", "captured-token")
  assert.equal(result.filename, `stock-research-${first}.json`)
  assert.equal(await result.blob.text(), '{"saved":true}')
})

test("export invalid input and HTML success responses do not trigger a download", async () => {
  assert.equal(typeof api.downloadStockResearchReport, "function")
  let called = false
  globalThis.fetch = async () => { called = true; return new Response("<html>login</html>", { headers: { "Content-Type": "text/html" } }) }
  await assert.rejects(api.downloadStockResearchReport("../escape", "json", "captured"))
  assert.equal(called, false)
  await assert.rejects(api.downloadStockResearchReport(first, "html", "captured"))
  assert.equal(called, false)
  await assert.rejects(api.downloadStockResearchReport(first, "json", "captured"), /类型/)
})

test("export size is bounded and HTTP resource failures retain their status", async () => {
  assert.equal(typeof api.downloadStockResearchReport, "function")
  globalThis.fetch = async () => new Response("{}", { headers: { "Content-Type": "application/json", "Content-Length": String(17 * 1024 * 1024) } })
  await assert.rejects(api.downloadStockResearchReport(first, "json", "captured"), /上限/)
  globalThis.fetch = async () => new Response('{"detail":"报告不存在"}', { status: 404, headers: { "Content-Type": "application/json" } })
  await assert.rejects(api.downloadStockResearchReport(first, "csv", "captured"), (failure) => failure.status === 404 && failure.message === "报告不存在")
})

test("comparison validates its pair, preserves null differences and binds returned resource IDs", async () => {
  assert.equal(typeof api.compareStockResearchReports, "function")
  const comparison = { comparison_version: "stock-saved-comparison-v1", kind: "backtest", left: { job: fixture(first), result: { metadata: {}, params: {}, metrics: {}, ic: null, factor_policies: [] } }, right: { job: fixture(second), result: { metadata: {}, params: {}, metrics: {}, ic: null, factor_policies: [] } }, compatibility: { comparable: false, same_method: false, same_workflow: true, same_data_version: true, same_requested_period: true, warnings: ["方法不同，不计算差值"] }, metrics: [{ key: "annualized_return_pct", label: "年化收益", unit: "percent", left: 2, right: 5, delta: null }], curves: { label: "组合净值", left: [{ date: "2026-10-16", nav: 1.02 }], right: [] } }
  let calls = 0
  globalThis.fetch = async (url, options) => { calls++; assert.equal(options.headers.Authorization, "Bearer captured"); assert.ok(url.includes(`left_id=${first}`)); return new Response(JSON.stringify(comparison), { headers: { "Content-Type": "application/json" } }) }
  await assert.rejects(api.compareStockResearchReports(first, first, "captured"))
  assert.equal(calls, 0)
  const result = await api.compareStockResearchReports(first, second, "captured")
  assert.equal(result.metrics[0].delta, null)
  comparison.right.job.id = first
  await assert.rejects(api.compareStockResearchReports(first, second, "captured"), /任务不匹配/)
})

test("error bodies obey a small streaming bound and preserve original HTTP status", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: "错".repeat(70 * 1024) }), { status: 503, headers: { "Content-Type": "application/json" } })
  await assert.rejects(api.downloadStockResearchReport(first, "json", "captured"), (failure) => failure.status === 503 && failure.message.length < 300)
})

test("download object URL and temporary anchor are released after a browser click", async () => {
  const oldDocument = globalThis.document, oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL
  const events = []
  const anchor = { style: {}, click() { events.push("click") }, remove() { events.push("remove") } }
  try {
    URL.createObjectURL = () => { events.push("create"); return "blob:qa" }
    URL.revokeObjectURL = (address) => events.push(address)
    globalThis.document = { body: { appendChild: () => events.push("append") }, createElement: () => anchor }
    api.startResearchReportDownload({ blob: new Blob(["report"]), filename: "saved.json" })
    await new Promise((resolve) => setTimeout(resolve, 5))
    assert.deepEqual(events, ["create", "append", "click", "remove", "blob:qa"])
    assert.equal(anchor.download, "saved.json")
  } finally { globalThis.document = oldDocument; URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke }
})
