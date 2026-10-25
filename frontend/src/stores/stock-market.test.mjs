import assert from "node:assert/strict"
import test from "node:test"

const values = new Map()
globalThis.window = {}
globalThis.localStorage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
}

test("stock context retains separate selections per user and login mode", async () => {
  const { useStockMarketStore } = await import("./stock-market.ts")
  const store = useStockMarketStore
  store.getState().restoreContext("owner", "virtual")
  store.getState().selectStock("000001")
  store.getState().restoreContext("other", "virtual")
  assert.equal(store.getState().activeStockCode, "600519")
  store.getState().selectStock("600036")
  store.getState().restoreContext("owner", "live")
  assert.equal(store.getState().activeStockCode, "600519")
  store.getState().restoreContext("owner", "virtual")
  assert.equal(store.getState().activeStockCode, "000001")
  assert.equal(values.has("qihuo_active_contract"), false)
})

test("stock and futures selectors reject the other market's identifiers", async () => {
  const { useStockMarketStore } = await import("./stock-market.ts")
  const { useAppStore, readActiveContract, readMarketViewContract } = await import("./app.ts")
  useStockMarketStore.getState().selectStock("rb2701")
  assert.equal(useStockMarketStore.getState().activeStockCode, "000001")
  useAppStore.getState().setActiveContract("rb2701")
  useAppStore.getState().setActiveContract("000001")
  useAppStore.getState().selectTradeSymbol("600519")
  assert.equal(useAppStore.getState().activeContract, "rb2701")
  values.set("qihuo_active_contract", "000001")
  values.set("qihuo_market_view_contract", "600519")
  assert.equal(readActiveContract(), null)
  assert.equal(readMarketViewContract(), null)
})

test("stock chart settings survive market remount and remain user scoped", async () => {
  const { useStockMarketStore } = await import("./stock-market.ts")
  useStockMarketStore.getState().restoreContext("owner", "virtual")
  useStockMarketStore.getState().setChartPreferences({ period: "week", showMACD: true, visible: 120 })
  useStockMarketStore.getState().restoreContext("other", "virtual")
  assert.equal(useStockMarketStore.getState().chartPreferences.period, "day")
  assert.equal(useStockMarketStore.getState().chartPreferences.showMACD, false)
  useStockMarketStore.getState().restoreContext("owner", "virtual")
  assert.equal(useStockMarketStore.getState().chartPreferences.period, "week")
  assert.equal(useStockMarketStore.getState().chartPreferences.showMACD, true)
  assert.equal(useStockMarketStore.getState().chartPreferences.visible, 120)
})

test("a stock deep link initializes identity before selection and is not overwritten by parent restore", async () => {
  const { useStockMarketStore } = await import("./stock-market.ts")
  useStockMarketStore.getState().restoreContext("deep-link", "virtual")
  useStockMarketStore.getState().selectStock("000001")
  useStockMarketStore.getState().restoreContext("other", "virtual")
  useStockMarketStore.getState().selectStockInContext("deep-link", "virtual", "600036")
  useStockMarketStore.getState().restoreContext("deep-link", "virtual")
  assert.equal(useStockMarketStore.getState().activeStockCode, "600036")
})

test("tick indicator and volume comparison preferences persist separately for user and JWT trading mode", async () => {
  const { useStockMarketStore } = await import("./stock-market.ts")
  const store = useStockMarketStore
  store.getState().restoreContext("tick-owner", "virtual")
  store.getState().setChartPreferences({ showKDJ: true, showRSI: true, showBOLL: true, volumeCompareEnabled: false, volumeCompareDays: 20, showCrosshair: false })
  store.getState().restoreContext("tick-owner", "live")
  assert.equal(store.getState().chartPreferences.showKDJ, false)
  assert.equal(store.getState().chartPreferences.showCrosshair, true)
  store.getState().restoreContext("tick-owner", "virtual")
  assert.equal(store.getState().chartPreferences.showKDJ, true)
  assert.equal(store.getState().chartPreferences.showRSI, true)
  assert.equal(store.getState().chartPreferences.showBOLL, true)
  assert.equal(store.getState().chartPreferences.volumeCompareEnabled, false)
  assert.equal(store.getState().chartPreferences.volumeCompareDays, 20)
  assert.equal(store.getState().chartPreferences.showCrosshair, false)
  store.getState().restoreContext("tick-other", "virtual")
  assert.equal(store.getState().chartPreferences.showRSI, false)
})

test("old chart settings retain their selected indicators while new options receive defaults", async () => {
  const { useStockMarketStore } = await import("./stock-market.ts")
  values.set("qihuo_stock_selection:tick-legacy:virtual:chart", JSON.stringify({ period: "week", visible: 120, showMA: false, showVolume: false, showMACD: true }))
  useStockMarketStore.getState().restoreContext("tick-legacy", "virtual")
  assert.deepEqual(useStockMarketStore.getState().chartPreferences, {
    period: "week", visible: 120, showMA: false, showVolume: false, showMACD: true,
    showRSI: false, showKDJ: false, showBOLL: false, showCrosshair: true,
    volumeCompareEnabled: true, volumeCompareDays: 1,
  })
})

test("corrupt stored options and runtime patches cannot introduce invalid chart periods or unbounded comparison windows", async () => {
  const { useStockMarketStore } = await import("./stock-market.ts")
  values.set("qihuo_stock_selection:tick-corrupt:virtual:chart", JSON.stringify({ period: "minute", visible: -1, showRSI: "yes", volumeCompareDays: -5 }))
  useStockMarketStore.getState().restoreContext("tick-corrupt", "virtual")
  assert.equal(useStockMarketStore.getState().chartPreferences.period, "day")
  assert.equal(useStockMarketStore.getState().chartPreferences.visible, 60)
  assert.equal(useStockMarketStore.getState().chartPreferences.showRSI, false)
  assert.equal(useStockMarketStore.getState().chartPreferences.volumeCompareDays, 1)
  useStockMarketStore.getState().setChartPreferences({ period: "minute", visible: NaN, showKDJ: "yes", volumeCompareDays: 1000 })
  assert.equal(useStockMarketStore.getState().chartPreferences.period, "day")
  assert.equal(useStockMarketStore.getState().chartPreferences.visible, 60)
  assert.equal(useStockMarketStore.getState().chartPreferences.showKDJ, false)
  assert.equal(useStockMarketStore.getState().chartPreferences.volumeCompareDays, 20)
})
