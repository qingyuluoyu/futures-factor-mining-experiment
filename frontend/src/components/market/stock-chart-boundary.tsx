"use client"

import { Component, type ReactNode } from "react"

function chartFailure(onRetry: () => void): React.JSX.Element {
  return <div role="alert" className="flex min-h-80 flex-col items-center justify-center gap-3 p-6 text-sm">
    <p className="text-[var(--text-secondary)]">图表暂时无法显示，请重试。</p>
    <button type="button" className="rounded border border-[var(--border)] px-4 py-2 text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]" onClick={onRetry}>重试图表</button>
  </div>
}

export function StockChartLoading({ error, onRetry }: { error?: unknown; onRetry: () => void }): React.JSX.Element {
  return error ? chartFailure(onRetry)
    : <p role="status" className="flex min-h-80 items-center justify-center text-sm text-[var(--text-muted)]">正在加载tick图表…</p>
}

export class StockChartBoundary extends Component<{ children: ReactNode; onRetry: () => void }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError(_error: unknown): { failed: boolean } { return { failed: true } }
  render(): ReactNode { return this.state.failed ? chartFailure(this.props.onRetry) : this.props.children }
}
