"use client"

import { useEffect, useRef, useState } from "react"
import { getStockFundamentals, type StockFundamentals } from "@/lib/stock-fundamentals-api"
import { useAuthStore } from "@/stores/auth"

function shanghaiToday(): string {
  const parts = new Intl.DateTimeFormat("en", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date())
  const part = (name: string) => parts.find((item) => item.type === name)?.value ?? ""
  return `${part("year")}-${part("month")}-${part("day")}`
}

const formatted = (value: number | null, suffix = ""): string => value === null ? "—" : `${value.toFixed(2)}${suffix}`
const control = "rounded border border-[var(--border)] bg-[var(--bg-primary)] px-3 py-2 text-xs disabled:opacity-40"

export function StockFundamentalsHistory({ code }: { code: string }): React.JSX.Element {
  const token = useAuthStore((state) => state.accessToken)
  const user = useAuthStore((state) => state.user)
  const [kind, setKind] = useState<"financial" | "valuation">("financial")
  const [asOf, setAsOf] = useState(shanghaiToday)
  const key = JSON.stringify([code, token, user?.id, user?.market_type, user?.trading_mode, kind, asOf])
  const latest = useRef(key)
  latest.current = key
  const generation = useRef(0)
  const controller = useRef<AbortController | null>(null)
  const [state, setState] = useState<{ key: string; offset: number; loading: boolean; data: StockFundamentals | null; error: string }>({ key, offset: 0, loading: false, data: null, error: "" })
  useEffect(() => {
    generation.current++
    controller.current?.abort()
    setState({ key, offset: 0, loading: false, data: null, error: "" })
    return () => { generation.current++; controller.current?.abort() }
  }, [key])
  const current = state.key === key ? state : null
  const data = current?.data
  const today = shanghaiToday()
  const canRead = Boolean(token && user?.market_type === "stock" && /^[0-9]{6}$/.test(code) && /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(asOf) && asOf <= today)

  async function read(offset: number) {
    if (!canRead || !token || !Number.isInteger(offset) || offset < 0 || offset > 100000) return
    controller.current?.abort()
    const request = new AbortController()
    controller.current = request
    const epoch = ++generation.current
    const active = () => latest.current === key && generation.current === epoch && !request.signal.aborted
    setState({ key, offset, loading: true, data: null, error: "" })
    try {
      const result = await getStockFundamentals({ code, kind, as_of: asOf, limit: 20, offset }, token, request.signal)
      if (active()) setState({ key, offset, loading: false, data: result, error: "" })
    } catch (failure: unknown) {
      if (!active()) return
      const requestId = failure && typeof failure === "object" && "request_id" in failure ? failure.request_id : null
      setState({ key, offset, loading: false, data: null, error: `${failure instanceof Error ? failure.message : "股票历史读取失败，请重试"}${requestId ? ` · 请求 ${requestId}` : ""}` })
    }
  }

  return <section aria-label="股票估值与披露历史" className="space-y-3 rounded-lg border border-[var(--border)] p-4">
    <h2 className="text-sm font-semibold">估值与披露历史查询</h2>
    <p className="text-xs leading-6 text-[var(--text-muted)]">按证券、截止日期分页读取已存数据，每页20条，不下载新数据。财报截止含公告当日，仅供展示，不是研究当日可交易许可；原表不保留修订版本，不能证明PIT历史时点。</p>
    <div className="flex flex-wrap items-center gap-3 text-xs">
      <label>记录类型 <select aria-label="历史记录类型" value={kind} onChange={(event) => setKind(event.target.value as "financial" | "valuation")} className={control}><option value="financial">财务披露历史</option><option value="valuation">每日估值历史</option></select></label>
      <label>截止日期 <input aria-label="历史截止日期" type="date" max={today} value={asOf} onChange={(event) => setAsOf(event.target.value)} className={control} /></label>
      <button type="button" className={control} disabled={!canRead || Boolean(current?.loading)} onClick={() => void read(current?.error ? current.offset : 0)}>{current?.loading ? "正在读取…" : current?.error ? "重试读取" : "读取历史"}</button>
    </div>
    {!canRead && <p role="alert" className="text-xs text-[var(--accent-danger)]">请使用A股登录会话、六位证券代码及不晚于上海今天的有效截止日期。</p>}
    {current?.error && <p role="alert" className="text-xs text-[var(--accent-danger)]">{current.error}</p>}
    {data && <>
      <p className="text-xs leading-6">{data.instrument.instrument_id} · 截止 {data.query.as_of} · 收录 {data.counts.source_rows} 条 · 符合截止 {data.counts.eligible_rows} · 缺公告 {data.counts.unknown_publication} · 日期异常 {data.counts.invalid_dates} · 晚于截止 {data.counts.later_rows}</p>
      {data.rows.length ? <div className="overflow-auto"><table className="w-full whitespace-nowrap text-right text-xs"><thead><tr>{(kind === "financial" ? ["报告期", "公告日期", "ROE", "毛利率", "营收同比", "利润同比", "EPS (元)", "BPS (元)", "归母净利润 (元)"] : ["交易日", "PE (TTM, 倍)", "PB (倍)", "总市值 (亿元)", "流通市值 (亿元)", "换手率"]).map((label) => <th key={label} className="px-3 py-2">{label}</th>)}</tr></thead><tbody>{data.rows.map((row) => {
        const cells = "report_date" in row ? [row.report_date, row.publish_date, formatted(row.roe, "%"), formatted(row.gross_margin, "%"), formatted(row.revenue_yoy, "%"), formatted(row.netprofit_yoy, "%"), formatted(row.eps), formatted(row.bps), formatted(row.netprofit)] : [row.trade_date, formatted(row.pe_ttm), formatted(row.pb), formatted(row.total_mv), formatted(row.circ_mv), formatted(row.turnover_rate, "%")]
        return <tr key={cells[0]} className="border-t border-[var(--border)] font-num">{cells.map((value, index) => <td key={index} className="px-3 py-2">{value}</td>)}</tr>
      })}</tbody></table></div> : <p role="status" className="text-xs text-[var(--text-muted)]">当前页没有符合截止条件的已存记录；不是数据源已成功补齐或证券停牌。</p>}
      <div className="flex items-center gap-3 text-xs"><button type="button" className={control} disabled={Boolean(current?.loading) || data.query.offset === 0} onClick={() => void read(Math.max(0, data.query.offset - 20))}>上一页</button><span>第 {Math.floor(data.query.offset / 20) + 1} 页 · 本页 {data.rows.length} 条</span><button type="button" className={control} disabled={Boolean(current?.loading) || !data.has_more || data.query.offset + 20 > 100000} onClick={() => void read(data.query.offset + 20)}>下一页</button></div>
      <div className="text-xs leading-6 text-[var(--text-muted)]"><p>来源：本地旧导入，来源授权/采集/单位仅Schema声明，未逐行认证；数据版本未留存。缺失显示“—”，负值不改零。</p>{data.limitations.map((item) => <p key={item}>{item}</p>)}<p>{data.history_version} · 读取 {data.read_at}</p></div>
    </>}
  </section>
}
