export type StockChartWindow =
  | { status: "ready"; from: number; to: number; visibleDateRange: { start: string; end: string } | undefined }
  | { status: "invalid"; message: string }
  | { status: "empty" }

function validDay(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const parsed = new Date(`${value}T00:00:00Z`)
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
}

/** Resolve a viewport over sorted loaded period bars. Never trim input or recompute indicators. */
export function resolveStockChartWindow(bars: readonly { time: string }[], start: string, end: string): StockChartWindow {
  if ((start && !validDay(start)) || (end && !validDay(end))) return { status: "invalid", message: "请选择有效的开始和结束日期。" }
  if (start && end && start > end) return { status: "invalid", message: "开始日期不能晚于结束日期。" }
  const from = bars.findIndex((bar) => (!start || bar.time >= start) && (!end || bar.time <= end))
  if (from < 0) return { status: "empty" }
  let to = bars.length - 1
  while (to > from && end && bars[to].time > end) to--
  return { status: "ready", from, to, visibleDateRange: start || end ? { start: bars[from].time, end: bars[to].time } : undefined }
}

/** Size the requested canvas from a bounded DOM viewport; renderer may grow for fixed subpanes. */
export function stockChartCanvasHeight(viewportHeight: number, controlsHeight: number, topOffset = 0): number {
  if (!Number.isFinite(viewportHeight) || viewportHeight <= 0) return 420
  const controls = Number.isFinite(controlsHeight) ? Math.max(0, controlsHeight) : 0
  const offset = Number.isFinite(topOffset) ? Math.max(0, topOffset) : 0
  return Math.max(320, Math.min(680, Math.floor(viewportHeight - controls - offset - 32)))
}
