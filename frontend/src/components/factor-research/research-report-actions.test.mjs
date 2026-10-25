import assert from "node:assert/strict"
import test from "node:test"
import { button, mountArtifact, settle, text } from "./report-artifact-test-harness.mjs"

test("report exports the actual selected format and reports a download start", async () => {
  const page = mountArtifact("actions"); await page.ready()
  await button(page.render(), "导出 JSON").props.onClick(); await settle()
  assert.equal(page.calls.exports.length, 1)
  assert.equal(page.calls.exports[0][1], "json")
  assert.equal(page.calls.exports[0][2], "captured")
  assert.equal(page.calls.starts.length, 1)
  assert.match(text(page.render()), /下载已开始/)
})

test("a stale export cannot download for a new session and duplicate clicks stay single-flight", async () => {
  const page = mountArtifact("actions"); await page.ready()
  let finish; page.controls.download = new Promise((resolve) => { finish = resolve })
  const first = button(page.render(), "导出 CSV").props.onClick()
  const second = button(page.render(), "导出 CSV").props.onClick()
  assert.equal(page.calls.exports.length, 1)
  page.controls.auth.sessionRevision++
  finish({ blob: {}, filename: "old.csv" })
  await Promise.all([first, second]); await settle()
  assert.equal(page.calls.starts.length, 0)
  await page.ready()
  assert.equal(button(page.render(), "导出 CSV").props.disabled, false)
})

test("export errors are visible and never pretend that a file was downloaded", async () => {
  const page = mountArtifact("actions"); await page.ready(); page.controls.error = "报告不存在"
  await button(page.render(), "导出 JSON").props.onClick(); await settle()
  assert.equal(page.calls.starts.length, 0)
  assert.match(text(page.render()), /报告不存在/)
})
