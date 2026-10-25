import assert from "node:assert/strict"
import test from "node:test"
import { button, mountArtifact, secondId, settle, text } from "./report-artifact-test-harness.mjs"

function savedComparison(comparable = true) {
  const side = { job: { title: "保存研究", method_version: "v4", data_version: "abc", workflow_version: "w1", params: { top_n: 10 } }, result: { params: {} } }
  return { left: side, right: side, compatibility: { comparable, same_method: comparable, same_data_version: true, same_workflow: true, same_requested_period: true, warnings: ["数据可获得时间未认证"] }, metrics: [{ key: "total_return_pct", label: "总收益", unit: "percent", left: 10, right: 12, delta: comparable ? 2 : null }], curves: { label: "保存净值", left: [], right: [] } }
}

test("comparison only fetches on explicit click and labels right-minus-left percentage points", async () => {
  const page = mountArtifact("comparison"); page.controls.comparison = savedComparison(); await page.ready()
  assert.equal(page.calls.compares.length, 0)
  await button(page.render(), "读取两份保存报告").props.onClick(); await settle()
  assert.equal(page.calls.compares.length, 1)
  assert.equal(page.calls.compares[0][1], secondId)
  assert.match(text(page.render()), /2\.00 个百分点/)
  assert.match(text(page.render()), /右减左/)
  assert.match(text(page.render()), /数据可获得时间未认证/)
})

test("incompatible reports show saved values without claiming comparable deltas", async () => {
  const page = mountArtifact("comparison"); page.controls.comparison = savedComparison(false); await page.ready()
  await button(page.render(), "读取两份保存报告").props.onClick(); await settle()
  assert.match(text(page.render()), /不可直接比较/)
  assert.match(text(page.render()), /不可比/)
  assert.doesNotMatch(text(page.render()), /2\.00 个百分点/)
})

test("late comparison is discarded on session revision or selection change and requests single-flight", async () => {
  const page = mountArtifact("comparison"); await page.ready()
  let finish; page.controls.comparison = new Promise((resolve) => { finish = resolve })
  const running = button(page.render(), "读取两份保存报告").props.onClick()
  await button(page.render(), "读取两份保存报告").props.onClick()
  assert.equal(page.calls.compares.length, 1)
  page.controls.auth.sessionRevision++
  finish(savedComparison())
  await running; await settle()
  assert.doesNotMatch(text(page.render()), /总收益/)
})

test("failed comparison preserves a visible error and allows retry without new jobs", async () => {
  const page = mountArtifact("comparison"); page.controls.error = "报告无权访问"; await page.ready()
  await button(page.render(), "读取两份保存报告").props.onClick(); await settle()
  assert.match(text(page.render()), /报告无权访问/)
  assert.equal(button(page.render(), "读取两份保存报告").props.disabled, false)
})

test("changing the selected pair aborts the old request and permits only a new explicit read", async () => {
  const page = mountArtifact("comparison"); await page.ready()
  let finish; page.controls.comparison = new Promise((resolve) => { finish = resolve })
  const running = button(page.render(), "读取两份保存报告").props.onClick()
  const signal = page.calls.compares[0][3]
  page.change({ rightId: "33333333-3333-4333-8333-333333333333" }); await page.ready()
  assert.equal(signal.aborted, true)
  finish(savedComparison()); await running; await settle()
  assert.doesNotMatch(text(page.render()), /总收益/)
  assert.equal(button(page.render(), "读取两份保存报告").props.disabled, false)
  assert.equal(page.calls.compares.length, 1)
})
