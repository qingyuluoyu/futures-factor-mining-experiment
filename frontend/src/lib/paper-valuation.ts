import type { PaperAccountSummary } from "@/lib/paper-api"

function finiteAmount(value: unknown): number | null {
  if (typeof value !== "number" && !(typeof value === "string" && /^-?\d+(\.\d+)?$/.test(value))) return null
  const amount = Number(value)
  return Number.isFinite(amount) ? amount : null
}

export function paperValuationView(account: PaperAccountSummary | null | undefined) {
  const valuation = account?.valuation
  const valued = valuation?.status === "cache_marked" || valuation?.status === "no_positions"
  return {
    equityLabel: valued ? "缓存参考权益" : "账面权益（不含浮盈）",
    equity: finiteAmount(valued ? valuation?.equity : valuation?.book_equity ?? account?.total_equity),
    floating: valued ? finiteAmount(valuation?.unrealized_pnl) : null,
    today: finiteAmount(valuation?.today_pnl),
    risk: valued ? finiteAmount(valuation?.risk_rate) : null,
    note: (valuation?.status === "unavailable" ? "持仓不可估值；缺价、来源不符或缓存已过期。" : "") + (valuation?.note ?? "未接通可信持仓估值，账面资金不包含浮盈；今日盈亏无交易日基准。"),
  }
}

export function signedPaperMoney(value: number | null): string {
  return value === null ? "—" : `${value >= 0 ? "+" : ""}${value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}
