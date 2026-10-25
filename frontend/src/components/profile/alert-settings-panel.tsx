"use client"

/**
 * 提醒设置面板 —— 价格预警列表 + 新建表单 + 通知开关
 *
 * 价格预警命中后自动停用，可重新启用。通知开关控制成交/资金/预警通知是否生成。
 */

import { useCallback, useEffect, useState } from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import {
  deletePriceAlertApi,
  getPriceAlertsApi,
  updatePriceAlertApi,
} from "@/lib/api"
import type { NotifySetting, PriceAlert } from "@/types"
import { useNotificationPreferencesStore } from "@/stores/notification-preferences"
import { PriceAlertForm } from "./price-alert-form"

export function AlertSettingsPanel(): React.JSX.Element {
  const [alerts, setAlerts] = useState<PriceAlert[]>([])
  const preferences = useNotificationPreferencesStore()
  const setting = preferences.settings
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const a = await getPriceAlertsApi()
      setAlerts(a)
    } catch {
      // 未登录静默
    }
  }, [])

  useEffect(() => {
    void load()
    void useNotificationPreferencesStore.getState().initialize()
  }, [load])

  async function toggleAlert(a: PriceAlert, enabled: boolean): Promise<void> {
    setBusy(true)
    try {
      const updated = await updatePriceAlertApi(a.id, enabled)
      setAlerts((prev) => prev.map((x) => (x.id === a.id ? updated : x)))
    } finally {
      setBusy(false)
    }
  }

  async function removeAlert(id: string): Promise<void> {
    setBusy(true)
    try {
      await deletePriceAlertApi(id)
      setAlerts((prev) => prev.filter((x) => x.id !== id))
    } finally {
      setBusy(false)
    }
  }

  async function toggleSetting(
    key: keyof NotifySetting,
    value: boolean,
  ): Promise<void> {
    await preferences.updateSettings({ [key]: value })
  }

  return (
    <div className="max-w-xl space-y-6">
      <h2 className="text-lg font-semibold text-[var(--text-primary)]">提醒设置</h2>

      <Card>
        <CardContent className="p-4 space-y-3">
          <p className="text-sm font-medium text-[var(--text-primary)]">价格预警</p>
          <Separator />
          <PriceAlertForm onCreated={() => void load()} />
          <PriceAlertList
            alerts={alerts}
            busy={busy}
            onToggle={toggleAlert}
            onDelete={removeAlert}
          />
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-4 space-y-4">
          <p className="text-sm font-medium text-[var(--text-primary)]">通知开关</p>
          <Separator />
          <ToggleRow
            label="弹出提醒"
            description="消息、大单与任务弹窗总开关；关闭后站内消息和未读仍保留，分类弹窗开关仍生效"
            checked={preferences.popupEnabled}
            disabled={!preferences.loaded || !preferences.supported || preferences.saving}
            onChange={(value) => void preferences.setPopupEnabled(value)}
          />
          {preferences.error && <p role="alert" className="text-xs text-[var(--accent-danger)]">{preferences.error}</p>}
          {!preferences.loaded && <Button variant="ghost" size="sm" onClick={() => void preferences.initialize()}>重新加载提醒偏好</Button>}
          <ToggleRow
            label="成交通知"
            description="订单成交后推送站内消息"
            checked={setting?.notify_trade ?? true}
            disabled={!setting || preferences.saving}
            onChange={(v) => void toggleSetting("notify_trade", v)}
          />
          <ToggleRow
            label="资金变动"
            description="领取模拟资金等资金变动通知"
            checked={setting?.notify_fund ?? true}
            disabled={!setting || preferences.saving}
            onChange={(v) => void toggleSetting("notify_fund", v)}
          />
          <ToggleRow
            label="价格预警"
            description="价格预警命中时推送通知"
            checked={setting?.notify_price_alert ?? true}
            disabled={!setting || preferences.saving}
            onChange={(v) => void toggleSetting("notify_price_alert", v)}
          />
          <ToggleRow
            label="消息提示音"
            description="新消息到达时播放提示音，与弹窗开关独立"
            checked={setting?.notify_sound_enabled ?? true}
            disabled={!setting || preferences.saving}
            onChange={(v) => void toggleSetting("notify_sound_enabled", v)}
          />
        </CardContent>
      </Card>
    </div>
  )
}

/** 价格预警列表 */
function PriceAlertList(props: {
  alerts: PriceAlert[]
  busy: boolean
  onToggle: (a: PriceAlert, enabled: boolean) => void
  onDelete: (id: string) => void
}): React.JSX.Element {
  if (props.alerts.length === 0) {
    return <p className="text-xs text-[var(--text-muted)] py-2">暂无价格预警</p>
  }
  return (
    <div className="space-y-2">
      {props.alerts.map((a) => (
        <div
          key={a.id}
          className="flex items-center justify-between py-2 px-3 rounded bg-[var(--bg-tertiary)]/50"
        >
          <div className="flex items-center gap-3 min-w-0">
            <span className="text-sm font-medium text-[var(--text-primary)] truncate">
              {a.contract_name}
            </span>
            <span className="text-xs text-[var(--text-muted)] shrink-0">
              {a.direction === "above" ? "≥" : "≤"}
              <span className="font-num ml-1">{a.target_price}</span>
            </span>
            {a.triggered_at && (
              <Badge variant="outline" className="text-[10px] shrink-0">
                已触发
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => props.onToggle(a, !a.enabled)}
              disabled={props.busy}
              className={cn(
                "w-8 h-4 rounded-full transition-colors relative cursor-pointer",
                a.enabled ? "bg-[var(--primary)]" : "bg-[var(--bg-tertiary)]",
              )}
            >
              <span
                className={cn(
                  "absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform",
                  a.enabled ? "left-[18px]" : "left-0.5",
                )}
              />
            </button>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-xs text-[var(--accent-danger)]"
              disabled={props.busy}
              onClick={() => props.onDelete(a.id)}
            >
              删除
            </Button>
          </div>
        </div>
      ))}
    </div>
  )
}

/** 开关行 */
function ToggleRow(props: {
  label: string
  description: string
  checked: boolean
  disabled: boolean
  onChange: (v: boolean) => void
}): React.JSX.Element {
  return (
    <div className="flex items-center justify-between py-1">
      <div>
        <p className="text-sm text-[var(--text-secondary)]">{props.label}</p>
        <p className="text-xs text-[var(--text-muted)] mt-0.5">{props.description}</p>
      </div>
      <button
        role="switch"
        aria-label={props.label}
        aria-checked={props.checked}
        onClick={() => props.onChange(!props.checked)}
        disabled={props.disabled}
        className={cn(
          "w-8 h-4 rounded-full transition-colors relative",
          props.checked ? "bg-[var(--primary)]" : "bg-[var(--bg-tertiary)]",
          props.disabled && "opacity-50 cursor-not-allowed",
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform",
            props.checked ? "left-[18px]" : "left-0.5",
          )}
        />
      </button>
    </div>
  )
}
