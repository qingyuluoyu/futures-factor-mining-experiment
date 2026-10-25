import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"

const nativeRequire = createRequire(import.meta.url)
const { create } = nativeRequire("zustand")
const bigOrder = nativeRequire("../components/market/big-order-data.mjs")

function setup(market = "futures", pending = false) {
  const auth = create(() => ({ user: market === null ? null : { id: "qa-user", market_type: market }, accessToken: "qa-fixture", marketSwitchPending: pending, loaded: true, hydrating: false }))
  const authHook = Object.assign((selector) => selector(auth.getState()), { getState: auth.getState, subscribe: auth.subscribe })
  const calls = { sound: 0, bigVoice: 0, taskVoice: 0, records: 0, marks: 0 }
  const settings = { ...bigOrder.DEFAULT_BIG_ORDER_SETTINGS, buy_enabled: true, sell_enabled: true, buy_threshold: 10, sell_threshold: 10, popup_enabled: true, sound_enabled: true, voice_enabled: true }
  const taskSettings = { open_enabled: true, close_enabled: true, popup_enabled: true, sound_enabled: true, voice_enabled: true }
  const modules = {}
  let onMessage
  const dependencies = {
    react: { useEffect: () => {} },
    "@/stores/auth": { useAuthStore: authHook },
    "@/lib/websocket": { getMarketWebSocket: () => ({ connect: () => {}, onMessage: (callback) => { onMessage = callback }, onStateChange: () => {}, onOpen: () => {} }) },
    "@/lib/api": { getQuotesSnapshotApi: async () => [], getUnreadCountApi: async () => 7 },
    "@/lib/sound": { playNotificationSound: () => calls.sound++ },
    "@/lib/speech": { speakBigOrder: () => calls.bigVoice++, speakTaskOrder: () => calls.taskVoice++ },
    "@/stores/big-order": { useBigOrderStore: { getState: () => ({ settings }) }, isBigOrderHit: bigOrder.isBigOrderHit, bigOrderColor: bigOrder.bigOrderColor },
    "@/stores/task-alert": { useTaskAlertStore: { getState: () => ({ settings: taskSettings }) }, isTaskAlertHit: (offset, state) => offset === "open" ? state.open_enabled : state.close_enabled },
    "@/stores/ai-market": { useAiMarketStore: Object.assign((selector) => selector({ tasks: [] }), { getState: () => ({ selectedTaskId: "qa-task", refreshMarks: () => calls.marks++, bumpRecords: () => calls.records++ }) }) },
    "@/stores/ai-anchor": { useAiAnchorStore: { getState: () => ({}) } },
    "@/lib/contract-names": { contractName: (symbol) => symbol },
    "@/lib/utils": { cn: (...values) => values.join(" ") },
    "@/lib/trade-labels": { marketTickDirectionLabel: (value) => value, paperActionLabel: (direction, offset) => `${direction}:${offset}` },
  }
  const sources = {
    "@/lib/notification-popup-policy": "../lib/notification-popup-policy.ts",
    "@/lib/futures-alert-policy": "../lib/futures-alert-policy.ts",
    "@/stores/notifications": "./notifications.ts",
    "@/stores/big-order-toast": "./big-order-toast.ts",
    "@/stores/task-alert-toast": "./task-alert-toast.ts",
  }
  function load(file, key) {
    if (modules[key]) return modules[key]
    const module = { exports: {} }
    modules[key] = module.exports
    const output = ts.transpileModule(readFileSync(new URL(file, import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
    vm.runInNewContext(output, { module, exports: module.exports, console, setTimeout, clearTimeout,
      require: (name) => dependencies[name] ?? (sources[name] ? load(sources[name], name) : nativeRequire(name)),
    })
    return module.exports
  }
  const policy = load("../lib/notification-popup-policy.ts", "@/lib/notification-popup-policy")
  policy.setNotificationPopupAllowed(true)
  const notifications = load("./notifications.ts", "@/stores/notifications").useNotificationsStore
  const big = load("./big-order-toast.ts", "@/stores/big-order-toast").useBigOrderToastStore
  const task = load("./task-alert-toast.ts", "@/stores/task-alert-toast").useTaskAlertToastStore
  dependencies["@/stores/big-order-toast"] = { useBigOrderToastStore: Object.assign((selector) => selector(big.getState()), { getState: big.getState, setState: big.setState }) }
  dependencies["@/stores/task-alert-toast"] = { useTaskAlertToastStore: Object.assign((selector) => selector(task.getState()), { getState: task.getState, setState: task.setState }) }
  const marketStore = load("./market.ts", "market").useMarketStore
  marketStore.getState().initWebSocket()
  const bigEvent = { type: "big_order_tick", data: { symbol: "rb-qa", direction: "sell", volume: 100, price: 100 } }
  const taskEvent = { type: "task_order_tick", data: { task_id: "qa-task", order_id: "qa-order", symbol: "rb-qa", direction: "buy", offset: "close", filled_qty: 1, price: 100 } }
  return { auth, calls, notifications, big, task, marketStore, settings, taskSettings, policy, send: (event) => onMessage(event),
    sendAlerts: () => { onMessage(bigEvent); onMessage(taskEvent) },
    renderBig: () => load("../components/notifications/big-order-toast.tsx", "bigComponent").BigOrderToastPopup(),
    renderTask: () => load("../components/notifications/task-alert-toast.tsx", "taskComponent").TaskAlertToastPopup(),
  }
}

function empty(tree) { return tree.props.children === undefined }

test("stock context suppresses futures alert popups and audio but keeps task record refresh", () => {
  const page = setup("stock")
  page.sendAlerts()
  assert.equal(page.big.getState().toasts.length, 0)
  assert.equal(page.task.getState().toasts.length, 0)
  assert.equal(page.calls.sound, 0)
  assert.equal(page.calls.bigVoice, 0)
  assert.equal(page.calls.taskVoice, 0)
  assert.equal(page.calls.records, 1)
  assert.equal(page.calls.marks, 1)
})

test("confirmed futures retains original threshold colors throttle dedup and task refresh", () => {
  const page = setup()
  page.sendAlerts(); page.sendAlerts()
  assert.equal(page.big.getState().toasts.length, 1)
  assert.equal(page.task.getState().toasts.length, 1)
  assert.equal(page.big.getState().toasts[0].color, page.settings.sell_color)
  assert.equal(page.task.getState().toasts[0].color, "#f59e0b")
  assert.equal(page.calls.sound, 2)
  assert.equal(page.calls.bigVoice, 1)
  assert.equal(page.calls.taskVoice, 1)
  assert.equal(page.calls.records, 2)
})

test("blocked stock events do not consume the futures throttle or order dedup slot", () => {
  const page = setup("stock")
  page.sendAlerts()
  assert.equal(page.big.getState().toasts.length, 0)
  assert.equal(page.task.getState().toasts.length, 0)
  page.auth.setState({ user: { id: "qa-user", market_type: "futures" } })
  page.sendAlerts()
  assert.equal(page.big.getState().toasts.length, 1)
  assert.equal(page.task.getState().toasts.length, 1)
  assert.equal(page.calls.bigVoice, 1)
  assert.equal(page.calls.taskVoice, 1)
})

test("unknown and switching markets fail closed until a futures context is confirmed", () => {
  for (const market of [null, "unknown", "stock", "futures"]) {
    const page = setup(market, true)
    page.sendAlerts()
    assert.equal(page.big.getState().toasts.length, 0)
    assert.equal(page.task.getState().toasts.length, 0)
    assert.equal(page.calls.sound, 0)
    page.auth.setState({ user: { id: "qa-user", market_type: "futures" }, marketSwitchPending: false })
    page.sendAlerts()
    assert.equal(page.big.getState().toasts.length, 1)
    assert.equal(page.task.getState().toasts.length, 1)
  }
})

test("stock trade scanning retains incoming futures quote and trade caches without generating alerts", () => {
  const page = setup("stock")
  const records = { "rb-qa": [{ time: "qa-time", direction: "buy", volume: 100, price: 100 }] }
  page.marketStore.getState().updateTrades(records)
  assert.equal(page.marketStore.getState().trades["rb-qa"][0].volume, 100)
  assert.equal(page.big.getState().toasts.length, 0)
  assert.equal(page.calls.sound, 0)
  page.send({ type: "quote", data: [{ symbol: "rb-qa", last_price: 100 }] })
  assert.equal(page.marketStore.getState().quotes["rb-qa"].last_price, 100)
  page.auth.setState({ user: { id: "qa-user", market_type: "futures" } })
  page.marketStore.getState().updateTrades(records)
  assert.equal(page.big.getState().toasts.length, 1)
})

test("missing login token never presents a futures alert even with a cached futures user", () => {
  const page = setup()
  page.auth.setState({ accessToken: null })
  page.sendAlerts()
  assert.equal(page.big.getState().toasts.length, 0)
  assert.equal(page.task.getState().toasts.length, 0)
  assert.equal(page.calls.sound, 0)
})

test("cached futures identity cannot emit or consume events before server session restoration finishes", () => {
  for (const patch of [{ loaded: false }, { hydrating: true }]) {
    const page = setup()
    page.auth.setState(patch)
    page.sendAlerts()
    assert.equal(page.big.getState().toasts.length, 0)
    assert.equal(page.task.getState().toasts.length, 0)
    assert.equal(page.calls.sound, 0)
    page.auth.setState({ loaded: true, hydrating: false })
    page.sendAlerts()
    assert.equal(page.big.getState().toasts.length, 1)
    assert.equal(page.task.getState().toasts.length, 1)
  }
})

test("futures global popup preference remains independent from voice and sound", () => {
  const page = setup()
  page.policy.setNotificationPopupAllowed(false)
  page.sendAlerts()
  assert.equal(page.big.getState().toasts.length, 0)
  assert.equal(page.task.getState().toasts.length, 0)
  assert.equal(page.calls.sound, 2)
  assert.equal(page.calls.bigVoice, 1)
  assert.equal(page.calls.taskVoice, 1)
})

test("stock still receives generic messages unread authority and their existing audio", () => {
  const page = setup("stock")
  page.send({ type: "notification", data: { id: "qa-system", category: "system", title: "QA generic notice" } })
  assert.equal(page.notifications.getState().popup.id, "qa-system")
  assert.equal(page.notifications.getState().unread, 1)
  assert.equal(page.calls.sound, 1)
  page.send({ type: "unread_count", data: { unread: 4 } })
  assert.equal(page.notifications.getState().unread, 4)
})

test("both popup views refuse preexisting futures queues in stock or pending context", () => {
  for (const [market, pending] of [["stock", false], ["futures", true], [null, false]]) {
    const page = setup()
    page.sendAlerts()
    assert.equal(empty(page.renderBig()), false)
    assert.equal(empty(page.renderTask()), false)
    page.auth.setState({ user: market === null ? null : { id: "qa-user", market_type: market }, marketSwitchPending: pending })
    assert.equal(empty(page.renderBig()), true)
    assert.equal(empty(page.renderTask()), true)
  }
})
