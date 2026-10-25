"use client"

import { useEffect, useRef, useState } from "react"
import { getStockReplayOrderHistory, type ReplayOrderHistory } from "@/lib/stock-replay-history-api"
import { useAuthStore } from "@/stores/auth"

const control = "rounded border border-[var(--border)] bg-[var(--bg-primary)] px-2 py-1.5 text-xs disabled:opacity-40"
const labels = { pending: "待处理", filled: "已成交", rejected: "已拒绝", cancelled: "已撤销" }

export function StockReplayOrderHistory({ accountId, revision, currentDate, disabled = false }: { accountId: string; revision: number; currentDate: string; disabled?: boolean }): React.JSX.Element {
  const token = useAuthStore((state) => state.accessToken)
  const user = useAuthStore((state) => state.user)
  const mode = user?.trading_mode === "virtual" ? "virtual" : "live"
  const [status, setStatus] = useState<"all" | "pending" | "filled" | "rejected" | "cancelled">("all")
  const [direction, setDirection] = useState<"all" | "buy" | "sell">("all")
  const [code, setCode] = useState("")
  const [start, setStart] = useState("")
  const [end, setEnd] = useState("")
  const key = JSON.stringify([token, user?.id, user?.market_type, mode, accountId, revision, currentDate, disabled, status, direction, code, start, end])
  const latest = useRef(key)
  latest.current = key
  const generation = useRef(0)
  const controller = useRef<AbortController | null>(null)
  const [state, setState] = useState<{ key: string; offset: number; loading: boolean; data: ReplayOrderHistory | null; error: string; refresh: boolean }>({ key, offset: 0, loading: false, data: null, error: "", refresh: false })
  useEffect(() => {
    generation.current++
    controller.current?.abort()
    setState({ key, offset: 0, loading: false, data: null, error: "", refresh: false })
    return () => { generation.current++; controller.current?.abort() }
  }, [key])
  const current = state.key === key ? state : null
  const data = current?.data
  const invalid = Boolean((code && !/^(?:00|60)[0-9]{4}$/.test(code)) || (start && end && start > end) || (start && start > currentDate) || (end && end > currentDate))
  const canRead = Boolean(!disabled && token && user?.market_type === "stock" && !invalid)

  async function read(offset: number) {
    if (!canRead || !token || current?.refresh || !Number.isInteger(offset) || offset < 0 || offset > 100000) return
    controller.current?.abort()
    const request = new AbortController()
    controller.current = request
    const epoch = ++generation.current
    const active = () => latest.current === key && generation.current === epoch && !request.signal.aborted
    setState({ key, offset, loading: true, data: null, error: "", refresh: false })
    try {
      const result = await getStockReplayOrderHistory(accountId, { expected_revision: revision, status, direction, code: code || null, start: start || null, end: end || null, limit: 20, offset }, token, mode, request.signal)
      if (active()) setState({ key, offset, loading: false, data: result, error: "", refresh: false })
    } catch (failure: unknown) {
      if (!active()) return
      const refresh = Boolean(failure && typeof failure === "object" && "requires_refresh" in failure && failure.requires_refresh === true)
      const requestId = failure && typeof failure === "object" && "request_id" in failure ? failure.request_id : null
      setState({ key, offset, loading: false, data: null, error: `${failure instanceof Error ? failure.message : "委托历史查询失败，请重试"}${requestId ? ` · 请求 ${requestId}` : ""}`, refresh })
    }
  }

  return <section aria-label="A股模拟委托全历史" className="max-h-[420px] shrink-0 space-y-3 overflow-auto border-b border-[var(--border)] p-3 text-xs">
    <h3 className="font-semibold">全部已存委托历史 · 服务端分页</h3>
    <p className="leading-6 text-[var(--text-muted)]">下面的原委托表仍显示待处理与最近200笔，可正常撤单。这里可查询更早的已存记录，每页20条；只读，不重放或执行交易。账户推进、委托或撤单变化后需刷新账户，避免分页混用不同revision。</p>
    <div className="flex flex-wrap items-center gap-2">
      <label>状态 <select aria-label="历史委托状态" value={status} onChange={(event) => setStatus(event.target.value as typeof status)} className={control}><option value="all">全部</option>{Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>方向 <select aria-label="历史委托方向" value={direction} onChange={(event) => setDirection(event.target.value as typeof direction)} className={control}><option value="all">全部</option><option value="buy">买入</option><option value="sell">卖出</option></select></label>
      <label>股票 <input aria-label="历史委托股票" value={code} maxLength={6} placeholder="六位代码，可空" onChange={(event) => setCode(event.target.value)} className={`${control} w-32`} /></label>
      <label>提交起日 <input aria-label="历史提交起日" type="date" max={currentDate} value={start} onChange={(event) => setStart(event.target.value)} className={control} /></label>
      <label>提交止日 <input aria-label="历史提交止日" type="date" max={currentDate} value={end} onChange={(event) => setEnd(event.target.value)} className={control} /></label>
      <button type="button" className={control} disabled={!canRead || Boolean(current?.loading) || Boolean(current?.refresh)} onClick={() => void read(current?.error ? current.offset : 0)}>{current?.loading ? "正在查询历史…" : current?.error ? "重试历史查询" : "查询全部委托历史"}</button>
    </div>
    {invalid && <p role="alert">代码需为账户内主板股票，日期顺序正确且不晚于回放游标 {currentDate}。</p>}
    {current?.error && <p role="alert" className="text-[var(--accent-danger)]">{current.error}</p>}
    {data && <>
      <p>匹配 {data.total} 笔 · 账户revision {data.revision} · 回放游标 {data.current_date} · 已存顺序，不是券商账单</p>
      {data.items.length ? <div className="overflow-auto"><table className="w-full whitespace-nowrap text-right"><thead><tr>{["提交日", "证券", "方向", "股数", "类型 / 限价", "状态 / 原因", "成交日", "成交价（元）", "费用（元）", "现金变动（元）", "已实现盈亏（元）"].map((label) => <th className="px-2 py-2" key={label}>{label}</th>)}</tr></thead><tbody>{data.items.map((item) => <tr className="border-t border-[var(--border)] font-num" key={item.id}>{[item.submitted_on, item.code, item.direction === "buy" ? "买入" : "卖出", String(item.quantity), item.order_type === "market" ? "市价" : item.limit_price, `${labels[item.status]}${item.reason ? ` / ${item.reason}` : ""}`, item.filled_on ?? "—", item.filled_price ?? "—", item.fees, item.cash_delta ?? "—", item.realized_pnl].map((cell, index) => <td className="px-2 py-2" key={index}>{cell}</td>)}</tr>)}</tbody></table></div> : <p role="status">没有匹配的委托；这是真实筛选结果，不代表账户从未交易。</p>}
      <div className="flex items-center gap-3"><button type="button" className={control} disabled={data.query.offset === 0 || Boolean(current?.loading) || !canRead} onClick={() => void read(Math.max(0, data.query.offset - 20))}>历史上一页</button><span>第 {Math.floor(data.query.offset / 20) + 1} 页 · 本页 {data.items.length} 笔</span><button type="button" className={control} disabled={!data.has_more || data.query.offset + 20 > 100000 || Boolean(current?.loading) || !canRead} onClick={() => void read(data.query.offset + 20)}>历史下一页</button></div>
      {data.query.offset + 20 > 100000 && data.has_more && <p>已到单次分页偏移边界，请缩小提交日期或证券筛选，不会后台全量拉取。</p>}
      <div className="space-y-1 leading-5 text-[var(--text-muted)]">{data.limitations.map((item) => <p key={item}>{item}</p>)}<p>{data.history_version} · 金额按已存Decimal字符串展示，未用浮点重算账户。</p></div>
    </>}
  </section>
}
