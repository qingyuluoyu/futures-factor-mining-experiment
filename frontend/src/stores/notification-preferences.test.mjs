import assert from "node:assert/strict"
import { readFileSync, existsSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"

const nativeRequire = createRequire(import.meta.url)
const defaults = { notify_trade: true, notify_fund: true, notify_price_alert: true, notify_sound_enabled: true, notify_popup_enabled: true }
const deferred = () => { let resolve, reject; const promise = new Promise((done, fail) => { resolve = done; reject = fail }); return { promise, resolve, reject } }

function setup(api = {}) {
  let auth = { user: { id: "a", market_type: "futures" }, accessToken: "a-token", sessionRevision: 1, marketSwitchPending: false, loaded: true, hydrating: false }
  const subscriptions = [], listeners = {}, writes = []
  const notifications = nativeRequire("zustand").create(() => ({ popup: { id: "old" }, unread: 7, dismissPopup() { notifications.setState({ popup: null }) } }))
  const big = nativeRequire("zustand").create(() => ({ toasts: ["old"] }))
  const task = nativeRequire("zustand").create(() => ({ toasts: ["old"] }))
  const loaded = {}
  const load = (file, key) => {
    const path = new URL(file, import.meta.url)
    assert.ok(existsSync(path), `${file} must implement notification preferences`)
    const output = ts.transpileModule(readFileSync(path, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
    const module = { exports: {} }
    vm.runInNewContext(output, { module, exports: module.exports, console, setTimeout, clearTimeout, AbortSignal, Error,
      window: { addEventListener: (type, callback) => { listeners[type] = callback } },
      localStorage: { setItem: () => {} },
      require: (name) => {
        if (name === "@/stores/auth") return { useAuthStore: { getState: () => auth, subscribe: (callback) => { subscriptions.push(callback) } } }
        if (name === "@/lib/api") return { getNotifySettingsApi: async () => ({ ...defaults }), updateNotifySettingsApi: async (body) => { writes.push(body); return { ...defaults, ...body } }, ...api }
        if (name === "@/lib/sound") return { setNotifySoundSettings: () => {} }
        if (name === "@/stores/notifications") return { useNotificationsStore: notifications }
        if (name === "@/stores/big-order-toast") return { useBigOrderToastStore: big }
        if (name === "@/stores/task-alert-toast") return { useTaskAlertToastStore: task }
        if (name === "@/lib/notification-popup-policy") return loaded.policy
        return nativeRequire(name)
      },
    })
    loaded[key] = module.exports
  }
  load("../lib/notification-popup-policy.ts", "policy")
  load("./notification-preferences.ts", "preferences")
  const change = (partial) => { const before = auth; auth = { ...auth, ...partial }; subscriptions.forEach((callback) => callback(auth, before)) }
  return { store: loaded.preferences.useNotificationPreferencesStore, allowed: loaded.policy.canShowNotificationPopup, notifications, big, task, writes, change, listeners, auth: { getState: () => auth, subscribe: (callback) => subscriptions.push(callback) } }
}

test("unloaded and failed preferences suppress popups; loaded defaults permit them", async () => {
  const page = setup()
  assert.equal(page.allowed(), false)
  await page.store.getState().initialize()
  assert.equal(page.allowed(), true)
  const failed = setup({ getNotifySettingsApi: async () => { throw new Error("offline") } })
  await failed.store.getState().initialize()
  assert.equal(failed.allowed(), false)
  assert.ok(failed.store.getState().error)
})

test("turning off clears all three queues, preserves unread, and persists actual saved value", async () => {
  const page = setup()
  await page.store.getState().initialize()
  await page.store.getState().setPopupEnabled(false)
  assert.equal(page.allowed(), false)
  assert.equal(page.notifications.getState().popup, null)
  assert.equal(page.notifications.getState().unread, 7)
  assert.equal(page.big.getState().toasts.length, 0)
  assert.equal(page.task.getState().toasts.length, 0)
  assert.equal(page.writes[0].notify_popup_enabled, false)
  await page.store.getState().setPopupEnabled(true)
  assert.equal(page.allowed(), true)
})

test("save failure rolls back and remains visible; old backend cannot report success", async () => {
  const page = setup({ updateNotifySettingsApi: async () => { throw new Error("save failed") } })
  await page.store.getState().initialize()
  await page.store.getState().setPopupEnabled(false)
  assert.equal(page.store.getState().popupEnabled, true)
  assert.match(page.store.getState().error, /save failed/)
  const old = setup({ getNotifySettingsApi: async () => { const { notify_popup_enabled, ...legacy } = defaults; return legacy } })
  await old.store.getState().initialize()
  assert.equal(old.store.getState().supported, false)
  assert.equal(old.allowed(), false)
  await old.store.getState().setPopupEnabled(false)
  assert.equal(old.writes.length, 0)
})

test("repeated initialize coalesces; late reads and writes cannot affect a different login", async () => {
  const read = deferred(), write = deferred()
  let reads = 0
  const page = setup({ getNotifySettingsApi: () => { reads++; return reads === 1 ? read.promise : Promise.resolve({ ...defaults, notify_popup_enabled: false }) }, updateNotifySettingsApi: () => write.promise })
  const first = page.store.getState().initialize(), duplicate = page.store.getState().initialize()
  assert.equal(reads, 1)
  page.change({ user: { id: "b" }, accessToken: "b-token", sessionRevision: 2 })
  await page.store.getState().initialize()
  read.resolve(defaults); await Promise.all([first, duplicate])
  assert.equal(page.store.getState().popupEnabled, false)
  const pending = page.store.getState().setPopupEnabled(true)
  await Promise.resolve()
  page.change({ user: null, accessToken: null, sessionRevision: 3 })
  write.resolve(defaults); await pending
  assert.equal(page.allowed(), false)
  assert.equal(page.store.getState().settings, null)
})

test("rapid toggles serialize backend writes and final state follows the latest intent", async () => {
  const first = deferred(), writes = []
  const page = setup({ updateNotifySettingsApi: async (body) => { writes.push(body.notify_popup_enabled); return writes.length === 1 ? first.promise : { ...defaults, ...body } } })
  await page.store.getState().initialize()
  const off = page.store.getState().setPopupEnabled(false), on = page.store.getState().setPopupEnabled(true)
  await Promise.resolve()
  assert.deepEqual(writes, [false])
  first.resolve({ ...defaults, notify_popup_enabled: false })
  await Promise.all([off, on])
  assert.deepEqual(writes, [false, true])
  assert.equal(page.store.getState().popupEnabled, true)
})

test("cross-tab signal re-reads server preference for the same user only", async () => {
  let value = true
  const page = setup({ getNotifySettingsApi: async () => ({ ...defaults, notify_popup_enabled: value }) })
  await page.store.getState().initialize()
  value = false
  await page.listeners.storage({ key: "notification-preferences:a", newValue: "changed" })
  await new Promise((resolve) => setTimeout(resolve, 0))
  assert.equal(page.allowed(), false)
})

test("saved response is authoritative and a backend missing the new field disables control", async () => {
  const mismatch = setup({ updateNotifySettingsApi: async () => ({ ...defaults, notify_popup_enabled: true }) })
  await mismatch.store.getState().initialize()
  await mismatch.store.getState().setPopupEnabled(false)
  assert.equal(mismatch.store.getState().popupEnabled, true)
  const missing = setup({ updateNotifySettingsApi: async () => { const { notify_popup_enabled, ...legacy } = defaults; return legacy } })
  await missing.store.getState().initialize()
  await missing.store.getState().setPopupEnabled(false)
  assert.equal(missing.store.getState().supported, false)
  assert.equal(missing.allowed(), false)
  assert.ok(missing.store.getState().error)
})

test("reload and login restore the server preference without replaying old popup queues", async () => {
  const page = setup({ getNotifySettingsApi: async () => ({ ...defaults, notify_popup_enabled: false }) })
  await page.store.getState().initialize()
  assert.equal(page.allowed(), false)
  page.change({ user: null, accessToken: null, sessionRevision: 2 })
  page.change({ user: { id: "a" }, accessToken: "new-a", sessionRevision: 3 })
  assert.equal(page.store.getState().loaded, false)
  await page.store.getState().initialize()
  assert.equal(page.allowed(), false)
  assert.equal(page.notifications.getState().popup, null)
})

test("actual WS dispatcher gates all three popup types but retains unread, record refresh, and independent audio", async () => {
  const page = setup()
  const pushes = { big: 0, task: 0, sound: 0, refresh: 0 }
  let onMessage
  const modules = {}
  function load(file, key) {
    const output = ts.transpileModule(readFileSync(new URL(file, import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
    const module = { exports: {} }
    vm.runInNewContext(output, { module, exports: module.exports, console, setTimeout, clearTimeout,
      require: (name) => {
        if (name === "@/stores/auth") return { useAuthStore: page.auth }
        if (name === "@/lib/futures-alert-policy") return nativeRequire("../lib/futures-alert-policy.ts")
        if (name === "@/lib/notification-popup-policy") return { canShowNotificationPopup: page.allowed }
        if (name === "@/lib/api") return { getQuotesSnapshotApi: async () => [], getUnreadCountApi: async () => 0 }
        if (name === "@/lib/websocket") return { getMarketWebSocket: () => ({ onMessage: (callback) => { onMessage = callback }, onStateChange: () => {}, onOpen: () => {}, connect: () => {} }) }
        if (name === "@/stores/notifications") return modules.notifications
        if (name === "@/lib/sound") return { playNotificationSound: () => pushes.sound++ }
        if (name === "@/lib/speech") return { speakBigOrder: () => {}, speakTaskOrder: () => {} }
        if (name === "@/stores/big-order") return { useBigOrderStore: { getState: () => ({ settings: { popup_enabled: true, sound_enabled: true } }) }, isBigOrderHit: () => true, bigOrderColor: () => "red" }
        if (name === "@/stores/task-alert") return { useTaskAlertStore: { getState: () => ({ settings: { popup_enabled: true, sound_enabled: true } }) }, isTaskAlertHit: () => true }
        if (name === "@/stores/big-order-toast") return { useBigOrderToastStore: { getState: () => ({ push: () => pushes.big++ }) } }
        if (name === "@/stores/task-alert-toast") return { useTaskAlertToastStore: { getState: () => ({ push: () => pushes.task++ }) } }
        if (name === "@/stores/ai-market") return { useAiMarketStore: { getState: () => ({ bumpRecords: () => pushes.refresh++ }) } }
        if (name === "@/stores/ai-anchor") return { useAiAnchorStore: { getState: () => ({}) } }
        return nativeRequire(name)
      },
    })
    modules[key] = module.exports
  }
  load("./notifications.ts", "notifications")
  load("./market.ts", "market")
  modules.market.useMarketStore.getState().initWebSocket()
  const send = (suffix) => {
    onMessage({ type: "notification", data: { id: suffix, title: "new", category: "trade" } })
    onMessage({ type: "big_order_tick", data: { symbol: `rb${suffix}`, direction: "buy", volume: 100 } })
    onMessage({ type: "task_order_tick", data: { task_id: "task", order_id: suffix, symbol: "rb", direction: "buy", offset: "open", filled_qty: 1 } })
  }
  send("unloaded")
  assert.equal(modules.notifications.useNotificationsStore.getState().popup, null)
  assert.deepEqual([pushes.big, pushes.task], [0, 0])
  await page.store.getState().initialize()
  send("enabled")
  assert.equal(modules.notifications.useNotificationsStore.getState().popup.id, "enabled")
  assert.deepEqual([pushes.big, pushes.task], [1, 1])
  await page.store.getState().setPopupEnabled(false)
  modules.notifications.useNotificationsStore.getState().dismissPopup()
  send("disabled")
  assert.equal(modules.notifications.useNotificationsStore.getState().popup, null)
  assert.deepEqual([pushes.big, pushes.task], [1, 1])
  assert.equal(modules.notifications.useNotificationsStore.getState().unread, 3)
  assert.equal(pushes.refresh, 3)
  assert.equal(pushes.sound, 9)
})

test("market-only transition clears futures transient queues without resetting generic messages or preferences", async () => {
  const page = setup()
  await page.store.getState().initialize()
  const settings = page.store.getState().settings
  page.notifications.setState({ popup: { id: "generic" } })
  page.big.setState({ toasts: ["futures-before-switch"] })
  page.task.setState({ toasts: ["futures-before-switch"] })
  page.change({ user: { id: "a", market_type: "stock" } })
  assert.equal(page.big.getState().toasts.length, 0)
  assert.equal(page.task.getState().toasts.length, 0)
  assert.equal(page.notifications.getState().popup.id, "generic")
  assert.equal(page.notifications.getState().unread, 7)
  assert.equal(page.store.getState().settings, settings)
  assert.equal(page.store.getState().loaded, true)
  assert.equal(page.allowed(), true)
  assert.equal(page.writes.length, 0)
})

test("pending market switches clear both transient queues even before server confirmation", async () => {
  const page = setup()
  await page.store.getState().initialize()
  page.big.setState({ toasts: ["futures-before-pending"] })
  page.task.setState({ toasts: ["futures-before-pending"] })
  page.change({ marketSwitchPending: true })
  assert.equal(page.big.getState().toasts.length, 0)
  assert.equal(page.task.getState().toasts.length, 0)
  assert.equal(page.notifications.getState().unread, 7)
  assert.equal(page.store.getState().loaded, true)
  page.change({ marketSwitchPending: false })
  assert.equal(page.allowed(), true)
})

test("market-only queue cleanup does not invalidate an in-flight preference save", async () => {
  const write = deferred()
  const page = setup({ updateNotifySettingsApi: () => write.promise })
  await page.store.getState().initialize()
  const saving = page.store.getState().setPopupEnabled(false)
  await Promise.resolve()
  page.change({ user: { id: "a", market_type: "stock" }, marketSwitchPending: true })
  write.resolve({ ...defaults, notify_popup_enabled: false })
  await saving
  assert.equal(page.store.getState().loaded, true)
  assert.equal(page.store.getState().saving, false)
  assert.equal(page.store.getState().popupEnabled, false)
  assert.equal(page.allowed(), false)
})
