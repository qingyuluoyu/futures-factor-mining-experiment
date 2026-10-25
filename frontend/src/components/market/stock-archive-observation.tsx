"use client"

import { useEffect, useRef, useState } from "react"
import { useAuthStore } from "@/stores/auth"
import { captureAiScope } from "@/lib/ai-client-scope"
import { getStockArchiveObservation, type StockArchiveObservationResult } from "@/lib/stock-archive-observation-api"

const control = "rounded border border-[var(--border)] bg-[var(--bg-secondary)] px-3 py-2 text-xs disabled:opacity-40"
const labels = { open: "开盘价", high: "最高价", low: "最低价", close: "收盘价", pre_close: "供应商除权昨收参考", volume: "成交量", amount: "成交额" } as const
const missingReasons = { source_missing: "来源快照缺少该证券，原因尚未核实，不代表停牌。", source_unknown_quarantined: "来源证券当时未匹配目录，已隔离；未填假行情。", outside_snapshot: "该证券不在此快照观察或已记录缺口内；不能推断停牌或用其他版本替代。" }

export function StockArchiveObservation({ version }: { version: string }): React.JSX.Element | null {
  const scopeKey = useAuthStore((state) => captureAiScope(state)?.key ?? null)
  const [draft, setDraft] = useState("")
  const [result, setResult] = useState<StockArchiveObservationResult | null>(null)
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)
  const mounted = useRef(false)
  const inflight = useRef(false)
  const generation = useRef(0)
  const controller = useRef<AbortController | null>(null)
  const latestVersion = useRef(version)
  latestVersion.current = version
  useEffect(() => useAuthStore.subscribe((next, previous) => {
    if (captureAiScope(next)?.key === captureAiScope(previous)?.key && next.accessToken === previous.accessToken) return
    generation.current++; controller.current?.abort(); inflight.current = false
    setResult(null); setError(""); setLoading(false); setDraft("")
  }), [])
  useEffect(() => {
    mounted.current = true; generation.current++; inflight.current = false
    setResult(null); setError(""); setLoading(false); setDraft("")
    return () => { mounted.current = false; generation.current++; controller.current?.abort(); inflight.current = false }
  }, [version])
  async function query() {
    const scope = captureAiScope(useAuthStore.getState())
    if (!mounted.current || inflight.current || !scope || scope.market !== "stock" || latestVersion.current !== version) return
    if (!/^(?:6[0-9]{5}\.SH|[03][0-9]{5}\.SZ|[489][0-9]{5}\.BJ)$/.test(draft)) { setError("请输入匹配交易所的标准证券标识，例如000001.SZ、600001.SH或920000.BJ；不接受裸代码。"); return }
    const request = new AbortController(), epoch = ++generation.current
    controller.current?.abort(); controller.current = request
    const active = () => mounted.current && !request.signal.aborted && controller.current === request && epoch === generation.current && latestVersion.current === version && captureAiScope(useAuthStore.getState())?.key === scope?.key && useAuthStore.getState().accessToken === scope?.accessToken
    inflight.current = true; setResult(null); setError(""); setLoading(true)
    try {
      const value = await getStockArchiveObservation(version, draft, scope.accessToken, request.signal)
      if (active()) setResult(value)
    } catch (failure) {
      if (active()) setError(failure instanceof Error ? failure.message : "固定版本原价核对失败")
    } finally {
      if (active()) { inflight.current = false; setLoading(false) }
    }
  }
  if (!scopeKey || captureAiScope(useAuthStore.getState())?.market !== "stock") return null
  const shown = result?.versions[0] === version ? result : null
  const point = shown?.observations[0], missing = shown?.missing[0], batch = shown?.batches[0]
  return <section aria-label="固定原价版本逐证券核对" className="space-y-3 rounded border border-[var(--border)] p-3 text-xs leading-6">
    <h3 className="font-semibold">固定原价版本逐证券核对</h3>
    <p className="break-all">指定归档版本：{version}</p>
    <p>只有提交才读取；每次核对一个版本、一只证券。全量比对该批次已存来源与影子观察后，只显示所选证券。不会抓取行情、调用模型或改账户。</p>
    <form className="flex flex-wrap gap-2" onSubmit={(event) => { event.preventDefault(); void query() }}>
      <label>标准证券标识<input aria-label="核对标准证券标识" className={control} value={draft} disabled={loading} placeholder="000001.SZ" onChange={(event) => { if (inflight.current) return; setDraft(event.target.value); setResult(null); setError("") }} /></label>
      <button type="submit" className={control} disabled={loading}>{loading ? "核对中…" : "核对固定版本原价"}</button>
    </form>
    {loading && <p role="status">正在核对来源与归档，最长约12秒；不自动重试或读取最新版本。</p>}
    {error && <p role="alert" className="text-[var(--accent-danger)]">{error}</p>}
    {shown && batch && <>
      <p>全量核对 {batch.verified_row_count} 条已存观察一致；同源一致不是独立真实性或PIT认证，不证明交易日历连续，不能直接用于回测或撮合。</p>
      <p>所选证券 {shown.instrument_ids[0]} · 核对生成 {shown.generated_at} · 数据为未复权日线、CNY；原始响应文本精度未经追认。</p>
      {missing && <p role="status">{missing.trade_date}：{missingReasons[missing.reason]}</p>}
      {point && <><p>交易日 {point.trade_date}</p><table className="w-full text-left"><thead><tr><th>字段</th><th>已存原值</th><th>单位</th></tr></thead><tbody>{(Object.keys(labels) as Array<keyof typeof labels>).map((field) => <tr key={field}><td>{labels[field]}</td><td className="break-all">{point[field]}</td><td>{field === "volume" ? "手（100股）" : field === "amount" ? "元" : "元/股"}</td></tr>)}</tbody></table></>}
      <p className="break-all">来源快照版本 {batch.source_data_version}</p><p>采集时间 {batch.source_collected_at}</p><p className="break-all">核对内容指纹 {shown.content_sha256}</p>
      <p>来源为用户已配置Teajoin兼容账户，未认证再分发授权；此结果不授予研究、下单或策略可靠性资格。</p>
    </>}
  </section>
}
