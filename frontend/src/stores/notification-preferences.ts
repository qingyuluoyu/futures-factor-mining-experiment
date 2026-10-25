"use client"

import { create } from "zustand"
import { getNotifySettingsApi, updateNotifySettingsApi } from "@/lib/api"
import { setNotificationPopupAllowed } from "@/lib/notification-popup-policy"
import { setNotifySoundSettings } from "@/lib/sound"
import { useAuthStore } from "@/stores/auth"
import { useNotificationsStore } from "@/stores/notifications"
import { useBigOrderToastStore } from "@/stores/big-order-toast"
import { useTaskAlertToastStore } from "@/stores/task-alert-toast"
import type { NotifySetting } from "@/types"

interface NotificationPreferencesState {
  loaded: boolean
  popupEnabled: boolean
  supported: boolean
  saving: boolean
  error: string | null
  settings: NotifySetting | null
  initialize: () => Promise<void>
  setPopupEnabled: (value: boolean) => Promise<void>
  updateSettings: (value: Partial<NotifySetting>) => Promise<void>
}

function identity(): string | null {
  const auth = useAuthStore.getState()
  return auth.user && auth.accessToken ? `${auth.user.id}:${auth.sessionRevision}` : null
}

function clearPopups(): void {
  useNotificationsStore.getState().dismissPopup()
  useBigOrderToastStore.setState({ toasts: [] })
  useTaskAlertToastStore.setState({ toasts: [] })
}

let owner: string | null = null
let generation = 0
let sequence = 0
let loading: Promise<void> | null = null
let writes: Promise<void> = Promise.resolve()
let confirmed: NotifySetting | null = null

function applyPolicy(settings: NotifySetting | null, supported: boolean): void {
  const enabled = supported && settings?.notify_popup_enabled === true
  setNotificationPopupAllowed(enabled)
  setNotifySoundSettings({ notify_sound_enabled: settings?.notify_sound_enabled === true })
  if (!enabled) clearPopups()
}

export const useNotificationPreferencesStore = create<NotificationPreferencesState>((set, get) => ({
  loaded: false,
  popupEnabled: false,
  supported: false,
  saving: false,
  error: null,
  settings: null,
  initialize: () => {
    const captured = identity()
    if (owner !== captured) reset(captured)
    if (!captured || get().loaded) return Promise.resolve()
    if (loading) return loading
    const revision = generation
    const current = async (): Promise<void> => {
      try {
        const settings = await getNotifySettingsApi()
        if (revision !== generation || identity() !== captured) return
        const supported = typeof settings.notify_popup_enabled === "boolean"
        confirmed = settings
        set({ settings, loaded: true, supported, popupEnabled: supported && settings.notify_popup_enabled === true, error: supported ? null : "当前服务尚不支持弹出提醒开关，请先升级服务" })
        applyPolicy(settings, supported)
      } catch (error) {
        if (revision !== generation || identity() !== captured) return
        set({ loaded: false, supported: false, popupEnabled: false, error: error instanceof Error ? error.message : "提醒偏好读取失败，请重试" })
        applyPolicy(null, false)
      } finally {
        if (revision === generation) loading = null
      }
    }
    loading = current()
    return loading
  },
  setPopupEnabled: (value) => get().updateSettings({ notify_popup_enabled: value }),
  updateSettings: (value) => {
    const captured = identity()
    if (!captured || owner !== captured || !get().loaded || !get().settings) return Promise.resolve()
    if ("notify_popup_enabled" in value && !get().supported) return Promise.resolve()
    const revision = generation
    const operation = ++sequence
    const optimistic = { ...get().settings!, ...value }
    set({ settings: optimistic, popupEnabled: get().supported && optimistic.notify_popup_enabled === true, saving: true, error: null })
    applyPolicy(optimistic, get().supported)
    const pending = writes.then(async () => {
      if (revision !== generation || identity() !== captured) return
      try {
        const saved = await updateNotifySettingsApi(value)
        if (revision !== generation || identity() !== captured) return
        if ("notify_popup_enabled" in value && typeof saved.notify_popup_enabled !== "boolean") {
          set({ supported: false })
          throw new Error("服务未保存弹出提醒偏好，请升级服务后重试")
        }
        confirmed = saved
        if (operation === sequence) {
          set({ settings: saved, popupEnabled: get().supported && saved.notify_popup_enabled === true, saving: false })
          applyPolicy(saved, get().supported)
        }
        try { localStorage.setItem(`notification-preferences:${useAuthStore.getState().user!.id}`, `${Date.now()}:${Math.random()}`) } catch {}
      } catch (error) {
        if (revision !== generation || identity() !== captured || operation !== sequence) return
        set({ settings: confirmed, popupEnabled: get().supported && confirmed?.notify_popup_enabled === true, saving: false, error: error instanceof Error ? error.message : "提醒偏好保存失败，请重试" })
        applyPolicy(confirmed, get().supported)
      }
    })
    writes = pending
    return pending
  },
}))

function reset(next: string | null): void {
  owner = next
  generation++
  sequence++
  loading = null
  writes = Promise.resolve()
  confirmed = null
  applyPolicy(null, false)
  useNotificationPreferencesStore.setState({ loaded: false, popupEnabled: false, supported: false, saving: false, error: null, settings: null })
}

useAuthStore.subscribe((state, previous) => {
  if (state.user?.market_type !== previous.user?.market_type || state.marketSwitchPending !== previous.marketSwitchPending) {
    useBigOrderToastStore.setState({ toasts: [] })
    useTaskAlertToastStore.setState({ toasts: [] })
  }
  const next = identity()
  if (owner !== next) reset(next)
})

if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    const user = useAuthStore.getState().user
    if (!user || event.key !== `notification-preferences:${user.id}`) return
    const captured = identity()
    void writes.then(() => {
      if (identity() !== captured) return
      useNotificationPreferencesStore.setState({ loaded: false })
      return useNotificationPreferencesStore.getState().initialize()
    })
  })
}
