const STOCK_ROUTES = new Set(["/market", "/market/screener", "/backtest", "/factor-lab", "/factor-research", "/history", "/ai-settings", "/profile", "/trading", "/positions", "/orders", "/assets", "/dashboard"])

export function supportsStockRoute(pathname: string): boolean {
  return STOCK_ROUTES.has(pathname)
}

export function supportsStockProfileTab(tab: string): boolean {
  return tab === "info" || tab === "display" || tab === "security"
}
