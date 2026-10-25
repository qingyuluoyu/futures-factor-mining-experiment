import assert from "node:assert/strict"
import test from "node:test"

test("stock routes expose the connected replay workspace and keep unfinished futures modules closed", async () => {
  const { supportsStockRoute } = await import("./market-routes.ts")
  for (const path of ["/market", "/market/screener", "/backtest", "/factor-lab", "/factor-research", "/history", "/ai-settings", "/profile", "/trading", "/positions", "/orders", "/assets", "/dashboard"]) assert.equal(supportsStockRoute(path), true)
  for (const path of ["/ai-market", "/ai-trading", "/strategy-favorites", "/market/not-connected", "/backtest/not-connected"]) assert.equal(supportsStockRoute(path), false)
})

test("stock profile cannot mount futures funds, margin settings or voice tools", async () => {
  const { supportsStockProfileTab } = await import("./market-routes.ts")
  for (const name of ["info", "display", "security"]) assert.equal(supportsStockProfileTab(name), true)
  for (const name of ["funds", "trading", "alerts", "voice"]) assert.equal(supportsStockProfileTab(name), false)
})
