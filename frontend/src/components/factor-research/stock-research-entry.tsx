"use client"

import Link from "next/link"
import { useRef, useState } from "react"
import { FactorResearchWorkspace } from "@/components/factor-research/factor-research-workspace"
import { StockModelLibrary } from "@/components/factor-research/stock-model-library"
import { StockFactorLibrary } from "@/components/factor-research/stock-factor-library"
import { StockPendingWritesBanner } from "@/components/factor-research/stock-pending-writes-banner"
import { stockResearchScope, type ResearchKind } from "@/lib/stock-research-api"
import { useAuthStore } from "@/stores/auth"

export type StockResearchView = "library" | ResearchKind

export function StockResearchEntry({ initialTab, title, view = initialTab ?? "analysis" }: { initialTab?: ResearchKind; title?: string; view?: StockResearchView }): React.JSX.Element {
  const user = useAuthStore((state) => state.user)
  const loaded = useAuthStore((state) => state.loaded)
  const token = useAuthStore((state) => state.accessToken)
  const sessionRevision = useAuthStore((state) => state.sessionRevision)
  const scope = stockResearchScope(user?.id, token)
  const stamp = JSON.stringify([loaded, user?.id, user?.market_type, token, scope, sessionRevision, view])
  const identity = useRef({ stamp })
  if (identity.current.stamp !== stamp) identity.current = { stamp }
  const [opened, setOpened] = useState<typeof identity.current | null>(null)
  if (!loaded) return <p role="status" className="p-4 text-sm">正在验证研究会话…</p>
  if (!scope || !token) return <p role="status" className="p-4 text-sm">研究会话已失效，<Link href="/login" className="underline">请重新登录</Link>。</p>
  if (user?.market_type !== "stock") return <p role="status" className="p-4 text-sm">请切换到A股市场后使用研究工作区。</p>
  const isOpen = opened === identity.current
  // 跨页未确认写横幅：本入口覆盖 /factor-research、/factor-lab、/backtest，
  // 共享侧栏已 SHA 锚定，所以在这里而不是侧栏提示。
  return <>
    <div className="px-4 pt-4"><StockPendingWritesBanner scope={scope} /></div>
    {view === "library" ? <StockFactorLibrary key={scope + token + sessionRevision + view} token={token} scope={scope} title={title} /> : <>
      {view === "analysis" && <div className="mb-3 px-4"><button type="button" aria-expanded={isOpen} className="cursor-pointer rounded border border-[var(--border)] px-3 py-2 text-xs" onClick={() => setOpened(isOpen ? null : identity.current)}>{isOpen ? "关闭本人模型历史" : "查看本人模型历史"}</button></div>}
      {view === "analysis" && isOpen && <StockModelLibrary key={scope + token + sessionRevision} token={token} scope={scope} />}
      <FactorResearchWorkspace key={scope + token + sessionRevision + view} token={token} scope={scope} initialTab={initialTab} view={view} title={title} />
    </>}
  </>
}
