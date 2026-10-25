"use client"

import { useEffect, useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import {
  getAdminSettingsApi,
  getRegistrationStatusApi,
  updateAdminSettingsApi,
  type RegistrationStatus,
  type SystemSettings,
} from "@/lib/admin-api"

const DEFAULT_SETTINGS: SystemSettings = {
  registration_enabled: true,
  daily_register_limit: 100,
  register_reset_hour: 6,
  paper_claim_amount: 1_000_000,
  paper_claim_period: "monthly",
  paper_claim_reset_hour: 6,
  paper_total_claim_cap: 0,
  system_name: "期货交易模拟系统",
  market_data_channel: "sina",
  backup_channel: "sina",
  channel_auto_switch: true,
  kline_source_mode: false,
  kline_source_order: "sina,tqsdk,eastmoney",
  kline_primary_source: "eastmoney",
  kline_backup_sources: "sina,tqsdk",
  kline_sync_enabled: true,
  max_tasks_per_user: 5,
  factor_lab_daily_limit: 0,
  backtest_daily_limit: 0,
  market_refresh_interval_sec: 1,
  market_refresh_intervals: {},
  vvtr_enabled: false,
  vvtr_api_key_masked: "",
  vvtr_api_key_set: false,
  vvtr_mobile: "",
  vvtr_base_url: "https://rest.vvtr.com/v1",
  vvtr_ws_url: "wss://rest.vvtr.com/v2/connect",
  vvtr_quotes_enabled: true,
  vvtr_ws_enabled: true,
  vvtr_kline_enabled: false,
  ctp_sim_enabled: false,
  ctp_sim_md_front: "",
  ctp_sim_broker_id: "9999",
  ctp_sim_investor_id: "qihuo_sim",
  ctp_sim_password_masked: "",
  ctp_sim_password_set: false,
  updated_at: null,
}

/** 系统设置 —— 注册策略 + 模拟资金 */
export default function AdminSettingsPage(): React.JSX.Element {
  const [form, setForm] = useState<SystemSettings>(DEFAULT_SETTINGS)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")
  const [registrationPolicy, setRegistrationPolicy] = useState<RegistrationStatus["registration_policy"] | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load(): Promise<void> {
      try {
        const [settings, registrationStatus] = await Promise.all([
          getAdminSettingsApi(),
          getRegistrationStatusApi().catch(() => null),
        ])
        if (!cancelled) {
          setForm(settings)
          setRegistrationPolicy(registrationStatus?.registration_policy ?? null)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "加载设置失败")
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [])

  function updateField<K extends keyof SystemSettings>(
    key: K,
    value: SystemSettings[K],
  ): void {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  async function handleSave(): Promise<void> {
    setSaving(true)
    setMessage("")
    setError("")
    try {
      const saved = await updateAdminSettingsApi({
        registration_enabled: form.registration_enabled,
        daily_register_limit: Number(form.daily_register_limit),
        register_reset_hour: Number(form.register_reset_hour),
        paper_claim_amount: Number(form.paper_claim_amount),
        paper_claim_period: form.paper_claim_period,
        paper_claim_reset_hour: Number(form.paper_claim_reset_hour),
        paper_total_claim_cap: Number(form.paper_total_claim_cap),
        system_name: form.system_name,
        max_tasks_per_user: Number(form.max_tasks_per_user),
      })
      setForm(saved)
      setMessage("设置已保存")
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败")
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="p-6 text-sm text-[var(--text-muted)]">加载设置…</div>
    )
  }

  return (
    <div className="p-6 space-y-6 max-w-3xl">
      <h1 className="text-lg font-semibold text-[var(--text-primary)]">
        系统设置
      </h1>

      {error && (
        <div className="px-4 py-2 rounded-md bg-[var(--accent-danger)]/15 text-[var(--accent-danger)] text-sm">
          {error}
        </div>
      )}
      {message && (
        <div className="px-4 py-2 rounded-md bg-[var(--primary)]/15 text-[var(--primary)] text-sm">
          {message}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>基础信息</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>系统名称</Label>
            <Input
              value={form.system_name}
              onChange={(e) => updateField("system_name", e.target.value)}
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>注册策略</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-[var(--text-muted)]">
            {registrationPolicy === "open"
              ? "当前为开放注册：下面的开关和每日名额不限制注册。"
              : registrationPolicy === "managed"
                ? "当前为受管注册：下面的开关和每日名额生效。"
                : "当前有效策略未获取，请检查公开注册状态接口。"}
            {" "}服务端 REGISTRATION_POLICY=managed 时应用下面保存的策略。
          </p>
          <div className="flex items-center justify-between p-3 rounded-md bg-[var(--bg-tertiary)]">
            <div>
              <p className="text-sm text-[var(--text-primary)]">受管模式注册开关</p>
              <p className="text-xs text-[var(--text-muted)] mt-0.5">
                受管模式下关闭后停止自助注册，管理员仍可后台创建
              </p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={form.registration_enabled}
              onClick={() =>
                updateField("registration_enabled", !form.registration_enabled)
              }
              className={`relative w-12 h-7 rounded-full transition-colors ${
                form.registration_enabled
                  ? "bg-[var(--primary)]"
                  : "bg-[var(--border)]"
              }`}
            >
              <span
                className={`absolute top-0.5 left-0.5 w-6 h-6 rounded-full bg-white transition-transform ${
                  form.registration_enabled ? "translate-x-5" : ""
                }`}
              />
            </button>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>每日注册人数上限</Label>
              <Input
                type="number"
                min={0}
                value={form.daily_register_limit}
                onChange={(e) =>
                  updateField(
                    "daily_register_limit",
                    Number(e.target.value || 0),
                  )
                }
              />
              <p className="text-xs text-[var(--text-muted)]">
                0 表示不限制人数
              </p>
            </div>
            <div className="space-y-2">
              <Label>注册计数重置时间（小时）</Label>
              <Input
                type="number"
                min={0}
                max={23}
                value={form.register_reset_hour}
                onChange={(e) =>
                  updateField(
                    "register_reset_hour",
                    Number(e.target.value || 0),
                  )
                }
              />
              <p className="text-xs text-[var(--text-muted)]">
                上海时区 0–23，默认 6（早 6 点重置）
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>模拟练手资金</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>单次可领取金额（元）</Label>
              <Input
                type="number"
                min={0}
                step={1000}
                value={form.paper_claim_amount}
                onChange={(e) =>
                  updateField(
                    "paper_claim_amount",
                    Number(e.target.value || 0),
                  )
                }
              />
            </div>
            <div className="space-y-2">
              <Label>领取周期</Label>
              <select
                className="w-full h-9 rounded-md border border-[var(--border)] bg-[var(--bg-primary)] px-2 text-sm"
                value={form.paper_claim_period}
                onChange={(e) =>
                  updateField(
                    "paper_claim_period",
                    e.target.value as "monthly" | "daily",
                  )
                }
              >
                <option value="monthly">按自然月</option>
                <option value="daily">按日（受重置小时影响）</option>
              </select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>领取重置时间（小时）</Label>
              <Input
                type="number"
                min={0}
                max={23}
                value={form.paper_claim_reset_hour}
                onChange={(e) =>
                  updateField(
                    "paper_claim_reset_hour",
                    Number(e.target.value || 0),
                  )
                }
              />
              <p className="text-xs text-[var(--text-muted)]">
                daily 周期生效；monthly 仍按自然月
              </p>
            </div>
            <div className="space-y-2">
              <Label>可领取总额上限（元）</Label>
              <Input
                type="number"
                min={0}
                step={1000}
                value={form.paper_total_claim_cap}
                onChange={(e) =>
                  updateField(
                    "paper_total_claim_cap",
                    Number(e.target.value || 0),
                  )
                }
              />
              <p className="text-xs text-[var(--text-muted)]">
                0 表示不限制用户累计领取总额
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>任务资源保护（全局默认）</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-xs text-[var(--text-muted)]">
            因子和回测使用次数不限，不设置每日或累计次数配额。这里仅设置活跃任务的资源保护；数据质量检查、单次模型 token、调用轮数和超时上限仍生效。
          </p>
          <div className="grid grid-cols-3 gap-4">
            <div className="space-y-2">
              <Label>最大任务数</Label>
              <Input
                type="number"
                min={0}
                value={form.max_tasks_per_user}
                onChange={(e) =>
                  updateField(
                    "max_tasks_per_user",
                    Number(e.target.value || 0),
                  )
                }
              />
              <p className="text-xs text-[var(--text-muted)]">默认 5</p>
            </div>
          </div>
        </CardContent>
      </Card>

      <Separator />

      <div className="flex justify-end gap-3">
        <Button onClick={() => void handleSave()} disabled={saving}>
          {saving ? "保存中…" : "保存设置"}
        </Button>
      </div>
    </div>
  )
}
