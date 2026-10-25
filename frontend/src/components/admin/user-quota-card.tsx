"use client"

/** 用户配额覆盖卡片 —— 管理员后台单独设置某用户的任务数/因子/回测配额 */

import { useEffect, useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import { getUserQuotaApi, updateAdminUserApi, type UserQuota } from "@/lib/admin-api"

interface UserQuotaCardProps {
  userId: string
}

function fmtLimit(v: number | null | undefined): string {
  return v == null ? "不限" : String(v)
}

export function UserQuotaCard({ userId }: UserQuotaCardProps): React.JSX.Element {
  const [quota, setQuota] = useState<UserQuota | null>(null)
  const [maxTasks, setMaxTasks] = useState("")
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState("")
  const [error, setError] = useState("")

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const q = await getUserQuotaApi(userId)
        if (cancelled) return
        setQuota(q)
        setMaxTasks(q.max_tasks_override?.toString() ?? "")
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "加载配额失败")
      }
    })()
    return () => {
      cancelled = true
    }
  }, [userId])

  async function handleSave(): Promise<void> {
    setSaving(true)
    setMsg("")
    setError("")
    try {
      await updateAdminUserApi(userId, {
        max_tasks_override: maxTasks === "" ? null : Number(maxTasks),
      })
      const q = await getUserQuotaApi(userId)
      setQuota(q)
      setMsg("配额已保存")
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败")
    } finally {
      setSaving(false)
    }
  }

  const eff = quota?.effective
  const used = quota?.used

  return (
    <Card>
      <CardHeader>
        <CardTitle>任务资源保护</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-[var(--text-muted)]">
          因子和回测使用次数不限，不设置每日或累计次数配额。任务数量仍受并发资源保护；空值使用全局任务配置。
        </p>
        <div className="grid grid-cols-3 gap-3">
          <div className="space-y-1">
            <Label>最大任务数</Label>
            <Input
              type="number"
              min={0}
              value={maxTasks}
              placeholder={`全局 ${fmtLimit(eff?.max_tasks)}`}
              onChange={(e) => setMaxTasks(e.target.value)}
            />
            {used && (
              <p className="text-[10px] text-[var(--text-muted)]">
                当前 {used.tasks} 个
              </p>
            )}
          </div>
          <p className="col-span-2 text-xs text-[var(--text-muted)]">取消业务次数配额不会取消数据质量检查、单次模型 token、调用轮数和超时上限。</p>
        </div>
        {msg && <p className="text-xs text-[var(--primary)]">{msg}</p>}
        {error && (
          <p className="text-xs text-[var(--accent-danger)]">{error}</p>
        )}
        <Button onClick={() => void handleSave()} disabled={saving}>
          {saving ? "保存中…" : "保存配额"}
        </Button>
      </CardContent>
    </Card>
  )
}
