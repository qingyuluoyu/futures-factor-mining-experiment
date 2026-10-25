import type { StockBoard } from "@/lib/stock-market-api"

export interface StockBoardSelection {
  keyword?: string
  sort?: "count" | "name" | "change_pct"
  direction?: "asc" | "desc"
  comparableOnly?: boolean
}

export function selectStockBoards(boards: StockBoard[], selection: StockBoardSelection): StockBoard[] {
  const keyword = (selection.keyword ?? "").trim()
  const sort = selection.sort ?? "count"
  const multiplier = selection.direction === "asc" ? 1 : -1
  return boards.filter((board) => board.industry.includes(keyword)
    && (!selection.comparableOnly || (board.comparable_count > 0 && board.change_pct !== null)))
    .sort((left, right) => {
      if (sort === "name") return left.industry.localeCompare(right.industry, "zh-CN") * multiplier
      const leftValue = sort === "count" ? left.count : left.change_pct
      const rightValue = sort === "count" ? right.count : right.change_pct
      if (leftValue === null || rightValue === null) {
        if (leftValue !== rightValue) return leftValue === null ? 1 : -1
      } else if (leftValue !== rightValue) return (leftValue - rightValue) * multiplier
      return left.industry < right.industry ? -1 : left.industry > right.industry ? 1 : 0
    })
}
