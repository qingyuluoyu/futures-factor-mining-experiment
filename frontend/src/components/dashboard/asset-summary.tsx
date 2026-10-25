"use client"

import { useEffect } from "react"
import { usePaperTradingStore } from "@/stores/paper-trading"
import { useAuthStore } from "@/stores/auth"
import { cn } from "@/lib/utils"
import { paperValuationView, signedPaperMoney } from "@/lib/paper-valuation"

/** 格式化金额 */
function formatMoney(value: number | null): string {
  if (value === null) return "—"
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

/** 实时资产摘要条 —— 模拟账户 */
export function AssetSummaryBar(): React.JSX.Element {
  const account = usePaperTradingStore((state) => state.account)
  const error = usePaperTradingStore((state) => state.error)
  const load = usePaperTradingStore((state) => state.refreshAccount)
  const token = useAuthStore((state) => state.accessToken)
  const sessionRevision = useAuthStore((state) => state.sessionRevision)

  useEffect(() => {
    load()
    const timer = setInterval(load, 8000)
    return () => clearInterval(timer)
  }, [load, token, sessionRevision])

  if (error && !account) {
    return (
      <div className="px-4 py-3 border-b border-[var(--border)] bg-[var(--bg-secondary)] text-xs text-[var(--text-muted)]">
        资产：{error}
      </div>
    )
  }

  if (!account) {
    return (
      <div className="px-4 py-3 border-b border-[var(--border)] bg-[var(--bg-secondary)] text-xs text-[var(--text-muted)]">
        资产加载中…
      </div>
    )
  }

  const valuation = paperValuationView(account)
  const items = [
    {
      label: valuation.equityLabel,
      value: formatMoney(valuation.equity),
      color: "text-[var(--text-primary)]",
    },
    {
      label: "可用保证金",
      value: formatMoney(account.available_margin),
      color: "text-[var(--text-primary)]",
    },
    {
      label: "浮动盈亏",
      value: signedPaperMoney(valuation.floating),
      color: valuation.floating === null ? "text-[var(--text-muted)]" : valuation.floating >= 0 ? "text-up" : "text-down",
    },
    {
      label: "今日盈亏",
      value: signedPaperMoney(valuation.today),
      color: "text-[var(--text-muted)]",
    },
    {
      label: "参考风险率",
      value: valuation.risk === null ? "—" : `${valuation.risk.toFixed(1)}%`,
      color:
        valuation.risk !== null && valuation.risk > 80
          ? "text-[var(--accent-danger)]"
          : "text-[var(--accent-info)]",
    },
  ]

  return (
    <div className="flex items-center gap-6 px-4 py-3 border-b border-[var(--border)] bg-[var(--bg-secondary)] overflow-x-auto">
      <span className="text-xs text-[var(--text-muted)] shrink-0" title={valuation.note}>{error ? "刷新失败，以下为上次数据" : "缓存参考，非结算；今日盈亏未核定"}</span>
      {items.map((item) => (
        <div key={item.label} className="flex items-center gap-2 shrink-0">
          <span className="text-xs text-[var(--text-muted)]">{item.label}</span>
          <span className={cn("font-num text-sm font-semibold", item.color)}>
            {item.value}
          </span>
        </div>
      ))}
    </div>
  )
}
