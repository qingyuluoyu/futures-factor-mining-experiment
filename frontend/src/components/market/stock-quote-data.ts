import type { StockBar } from "@/lib/stock-api"
import { prepareStockBars } from "./stock-chart-data.ts"

export function validateStockQuoteBars(bars: StockBar[]): StockBar[] {
  const prepared = prepareStockBars(bars)
  if (prepared.length !== bars.length) throw new Error("日线数据异常（日期重复或OHLC/成交量无效），停止展示报价及指标，请联系管理员核对数据源")
  return prepared
}
