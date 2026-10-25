"use client"

import { useEffect, useRef, useState } from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { getProfileApi, updateProfileApi } from "@/lib/api"
import { useAuthStore } from "@/stores/auth"
import type { User } from "@/types"

function contactFields(user: User | null) { return { phone: user?.phone ?? "", email: user?.email ?? "" } }
function personalFields(user: User): Partial<User> {
  return { username: user.username, phone: user.phone, email: user.email, avatar: user.avatar,
    real_name_verified: user.real_name_verified, realNameVerified: user.realNameVerified,
    created_at: user.created_at, createdAt: user.createdAt, updated_at: user.updated_at }
}

export function PersonalInfoPanel(): React.JSX.Element {
  const { user, accessToken, sessionRevision, updateUser } = useAuthStore()
  const [profile, setProfile] = useState<User | null>(user)
  const [draft, setDraft] = useState(() => contactFields(user))
  const [editing, setEditing] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loaded, setLoaded] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [reload, setReload] = useState(0)
  const savingRef = useRef(false)
  const mounted = useRef(true)
  const isCurrent = () => mounted.current && useAuthStore.getState().sessionRevision === sessionRevision && useAuthStore.getState().user?.id === user?.id

  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => {
    let active = true
    setLoading(true); setLoaded(false); setError("")
    if (!user || !accessToken) { setLoading(false); return () => { active = false } }
    void getProfileApi(accessToken).then((data) => {
      if (!active || !isCurrent()) return
      if (data.id !== user.id) throw new Error("资料响应与当前用户不一致")
      setProfile(data); setDraft(contactFields(data)); setLoaded(true)
      updateUser(personalFields(data))
    }).catch((failure) => {
      if (active && isCurrent()) setError(failure instanceof Error ? failure.message : "资料加载失败")
    }).finally(() => { if (active && isCurrent()) setLoading(false) })
    return () => { active = false }
  }, [user?.id, sessionRevision, reload])

  const cancel = () => { if (savingRef.current) return; setDraft(contactFields(profile)); setEditing(false); setError(""); setNotice("") }
  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!profile || !accessToken || !loaded || !isCurrent() || savingRef.current) return
    setError(""); setNotice("")
    const patch: Partial<User> = {}
    for (const key of ["phone", "email"] as const) {
      const value = draft[key].trim()
      if (value === (profile[key] ?? "")) continue
      if (!value && profile[key]) { setError(`暂不支持清空已绑定${key === "phone" ? "手机号" : "邮箱"}，可修改为新的有效值`); return }
      if (value) patch[key] = value
    }
    if (!Object.keys(patch).length) { setEditing(false); setNotice("资料未更改"); return }
    savingRef.current = true; setSaving(true)
    try {
      const data = await updateProfileApi(accessToken, patch)
      if (!isCurrent()) return
      if (data.id !== user?.id) throw new Error("保存响应与当前用户不一致")
      setProfile(data); setDraft(contactFields(data)); updateUser(personalFields(data)); setEditing(false); setNotice("资料已保存")
    } catch (failure) {
      if (isCurrent()) setError(failure instanceof Error ? failure.message : "资料保存失败")
    } finally {
      savingRef.current = false
      if (isCurrent()) setSaving(false)
    }
  }
  const registered = profile?.created_at ?? profile?.createdAt
  const date = registered ? new Date(registered) : null
  const registeredText = date && Number.isFinite(date.getTime()) ? date.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "未提供"
  const verified = profile?.real_name_verified ?? profile?.realNameVerified ?? false

  return <section className="max-w-xl space-y-4">
    <h2 className="text-lg font-semibold text-[var(--text-primary)]">个人信息</h2>
    {loading && <p role="status" className="text-xs text-[var(--text-muted)]">正在核对本人资料…</p>}
    {error && <div role="alert" className="text-sm text-[var(--accent-danger)]">{error}{!loaded && <Button variant="ghost" onClick={() => setReload((value) => value + 1)} disabled={loading}>重试加载</Button>}</div>}
    {notice && <p role="status" className="text-sm text-[var(--text-secondary)]">{notice}</p>}
    <Card><CardContent className="flex items-center gap-4 p-4">
      <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-[var(--primary)] text-xl text-white">{profile?.username?.slice(0, 1) ?? "—"}</div>
      <div className="min-w-0 flex-1"><p className="truncate text-sm">{profile?.username ?? "未登录"}</p><p className="break-all text-xs text-[var(--text-muted)]">ID: {profile?.id ?? "未提供"}</p></div>
      <Button variant="outline" size="sm" disabled title="头像上传尚未开放">更换头像（未开放）</Button>
    </CardContent></Card>
    <Card><CardContent className="p-4">
      <form onSubmit={save} className="space-y-4">
        <div className="flex items-center justify-between"><h3 className="text-sm">基本信息</h3><Button type="button" variant="ghost" disabled={!loaded || saving} onClick={() => { if (editing) cancel(); else { setEditing(true); setNotice("") } }}>{editing ? "取消" : "编辑"}</Button></div>
        <div className="flex justify-between text-sm"><span>用户名（只读）</span><span>{profile?.username ?? "未提供"}</span></div>
        {([{ key: "phone", label: "手机号", type: "tel", max: 20 }, { key: "email", label: "邮箱", type: "email", max: 100 }] as const).map((field) => <label key={field.key} className="flex items-center justify-between gap-3 text-sm">
          <span>{field.label}</span>{editing ? <Input aria-label={field.label} type={field.type} maxLength={field.max} value={draft[field.key]} disabled={saving} onChange={(event) => setDraft((current) => ({ ...current, [field.key]: event.target.value }))} className="w-64" /> : <span>{profile?.[field.key] || "未绑定"}</span>}
        </label>)}
        <div className="flex items-center justify-between text-sm"><span>实名认证</span><Badge variant={verified ? "up" : "outline"}>{verified ? "已认证" : "未认证"}</Badge></div>
        <p className="text-xs text-[var(--text-muted)]">仅展示账户已有认证状态，模拟交易不要求接入真实资金账户。</p>
        <div className="flex justify-between gap-3 text-sm"><span>注册时间（北京时间）</span><span>{registeredText}</span></div>
        {editing && <div className="flex justify-end gap-2"><Button type="button" variant="outline" disabled={saving} onClick={cancel}>取消</Button><Button type="submit" disabled={saving}>{saving ? "保存中…" : "保存"}</Button></div>}
      </form>
    </CardContent></Card>
  </section>
}
