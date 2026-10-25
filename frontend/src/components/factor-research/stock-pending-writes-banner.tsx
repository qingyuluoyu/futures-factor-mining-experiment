"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { readPendingWrites, type PendingWriteEntry } from "@/lib/stock-pending-writes"

type Destination = { href: string; label: string }
type Group = { destination: Destination | null; kinds: string[]; count: number }

/** 每种未确认写的归属页面：写发生在哪，就回哪用原幂等键解决。 */
function destinationOf(kind: string): Destination | null {
  if (kind.startsWith("replay-")) return { href: "/trading", label: "交易回放写入" }
  if (kind === "dataset-job" || kind === "report-bookmark" || kind.startsWith("custom-factor-")) return { href: "/factor-research", label: "因子研究写入" }
  return null
}

/** 未登记的 kind 单独成组，绝不因为不认识就被并掉或丢弃。 */
function groupEntries(entries: readonly PendingWriteEntry[]): Group[] {
  const order: string[] = []
  const groups: Record<string, Group> = {}
  for (const item of entries) {
    const destination = destinationOf(item.kind)
    const key = destination ? destination.href : `unknown:${item.kind}`
    if (!(key in groups)) { order.push(key); groups[key] = { destination, kinds: [], count: 0 } }
    groups[key].kinds.push(item.kind)
    groups[key].count += 1
  }
  return order.map((key) => groups[key])
}

/**
 * 股票独占的跨页「未确认写」横幅。
 *
 * 共享侧栏与主布局都已 SHA 锚定且双市场共用，所以不加 `beforeunload`/路由拦截守卫；
 * 改为从已挂载在股票路由上的独占组件渲染本横幅，页内先例见
 * `stock-replay-workspace.tsx` 的 `locked` 导航降级。
 *
 * 只读注册表、只提示，**不代替用户重写任何请求**：重试一律由写发生处的组件用原幂等键发起。
 * 未登记的 kind 也照样提示（只是没有链接），绝不因为不认识就静默丢弃。
 */
export function StockPendingWritesBanner({ scope, ignore }: { scope: string; ignore?: (kind: string) => boolean }): React.JSX.Element | null {
  const [entries, setEntries] = useState<readonly PendingWriteEntry[]>([])
  // ignore 是内联函数，放进依赖会每次渲染重跑 effect；用 ref 读最新值，依赖只留 scope。
  const ignoreRef = useRef(ignore)
  ignoreRef.current = ignore
  useEffect(() => {
    setEntries(readPendingWrites(scope).filter((item) => !ignoreRef.current?.(item.kind)))
  }, [scope])
  if (!entries.length) return null
  const groups = groupEntries(entries)
  return <div role="status" className="mb-3 space-y-1 rounded border border-[var(--border)] bg-[var(--bg-secondary)] px-3 py-2 text-xs text-[var(--text-secondary)]">
    <p>有{entries.length}次写入的结果尚未确认；结果未知，未自动重写。请按原幂等键重试，不要换键重复创建。</p>
    <ul className="space-y-0.5">
      {groups.map((group) => <li key={group.destination ? group.destination.href : group.kinds[0]} className="break-all">
        {group.kinds.slice().sort().join("、")} 共{group.count}次
        {group.destination ? <>，去<Link href={group.destination.href} className="underline">{group.destination.label}</Link>重试同一请求</> : "，本页无对应重试入口"}
      </li>)}
    </ul>
  </div>
}
