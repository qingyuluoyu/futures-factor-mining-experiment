import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import { jsx } from "react/jsx-runtime"
import { renderToStaticMarkup } from "react-dom/server"
import { loadTsx } from "../dashboard/dashboard-frame-harness.mjs"

const load = () => loadTsx(readFileSync(new URL("./stock-chart-boundary.tsx", import.meta.url), "utf8"))
function nodes(tree) { return Array.isArray(tree) ? tree.flatMap(nodes) : tree && typeof tree === "object" ? [tree, ...nodes(tree.props?.children)] : [] }

test("chart loading failure has a manual action without exposing raw diagnostics", () => {
  const { StockChartLoading } = load()
  let retries = 0
  const tree = StockChartLoading({ error: new Error("private-url-and-diagnostics"), onRetry: () => retries++ })
  const markup = renderToStaticMarkup(tree)
  assert.match(markup, /role="alert"/)
  assert.match(markup, /重试图表/)
  assert.doesNotMatch(markup, /正在加载|private-url|diagnostics/)
  assert.equal(retries, 0)
  nodes(tree).find((node) => node.type === "button").props.onClick()
  assert.equal(retries, 1)
})

test("pending loading has no retry loop and runtime error is scoped to its chart boundary", () => {
  const { StockChartLoading, StockChartBoundary } = load()
  const pending = renderToStaticMarkup(jsx(StockChartLoading, { onRetry: () => assert.fail("not automatic") }))
  assert.match(pending, /role="status"/)
  assert.doesNotMatch(pending, /role="alert"|<button/)
  let retries = 0
  const boundary = new StockChartBoundary({ children: jsx("span", { children: "current-chart" }), onRetry: () => retries++ })
  assert.match(renderToStaticMarkup(boundary.render()), /current-chart/)
  boundary.state = StockChartBoundary.getDerivedStateFromError(new Error("private-render-error"))
  const fallback = boundary.render()
  assert.doesNotMatch(renderToStaticMarkup(fallback), /current-chart|private-render-error/)
  const retry = nodes(fallback).find((node) => node.type === "button")
  assert.ok(retry)
  retry.props.onClick()
  assert.equal(retries, 1)
})
