"use client"

import { useEffect, useRef, useState } from "react"
import { getStockResearchInputs, type StockResearchInputQuery, type StockResearchInputs } from "@/lib/stock-research-inputs-api"

const reasonLabels = { no_valid_stock_prices: "无正有效股票收盘价", insufficient_observed_dates: "观测日不足必要窗口", no_usable_field_values: "依赖字段没有金融域有效值" }

export function StockResearchInputCheck({ query, token, scope, disabled = false }: { query: StockResearchInputQuery; token: string; scope: string; disabled?: boolean }): React.JSX.Element {
  const key = JSON.stringify([token, scope, query.start_date, query.end_date, [...query.factor_ids].sort(), query.mktcap_neutral ?? false, query.weight ?? "equal"])
  const latest = useRef(key)
  latest.current = key
  const generation = useRef(0)
  const controllerRef = useRef<AbortController | null>(null)
  const [state, setState] = useState<{ key: string; loading: boolean; data: StockResearchInputs | null; error: string }>({ key, loading: false, data: null, error: "" })
  useEffect(() => {
    generation.current++
    controllerRef.current?.abort()
    setState({ key, loading: false, data: null, error: "" })
    return () => { generation.current++; controllerRef.current?.abort() }
  }, [key])
  const current = state.key === key ? state : null

  async function inspect() {
    if (disabled || !query.start_date || !query.end_date) return
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    const epoch = ++generation.current
    const active = () => latest.current === key && generation.current === epoch && !controller.signal.aborted
    setState({ key, loading: true, data: null, error: "" })
    try {
      const data = await getStockResearchInputs(query, token, controller.signal)
      if (active()) setState({ key, loading: false, data, error: "" })
    } catch (failure: unknown) {
      if (!active()) return
      const requestId = failure && typeof failure === "object" && "request_id" in failure ? failure.request_id : null
      setState({ key, loading: false, data: null, error: `${failure instanceof Error ? failure.message : "研究输入检查失败，请重试"}${requestId ? ` · 请求 ${requestId}` : ""}` })
    }
  }

  const data = current?.data
  return <section aria-label="所选股票因子输入检查" className="mb-4 space-y-2 rounded border border-[var(--border)] p-3 text-xs leading-6">
    <div className="flex flex-wrap items-center gap-3"><strong>因子输入资格解释</strong><button type="button" disabled={disabled || current?.loading || !query.start_date || !query.end_date} onClick={() => void inspect()} className="underline disabled:opacity-40">{current?.loading ? "正在检查当前输入…" : current?.error ? "重试因子输入检查" : "检查当前因子输入"}</button></div>
    <p className="text-[var(--text-muted)]">显式只读检查当前日期、因子及市值依赖；不提交任务、不下载数据。容量够不代表存在有效输入；修改参数、身份或登录盘后需重新检查。</p>
    {current?.error && <p role="alert" className="text-[var(--accent-danger)]">{current.error}</p>}
    {data && <>
      <p role={data.status === "potential_inputs" ? "status" : "alert"}>{data.status === "invalid_inputs" ? "价格存在非法值或重复，不能据此认定具备研究资格。" : data.status === "missing_inputs" ? "所选配置缺少必要输入，请检查以下原因，不必反复盲提交。" : "找到候选输入；不是因子计算通过、研究成功或PIT认证。"}</p>
      <p>{data.query.start_date} 至 {data.query.end_date} · 股票价格 {data.prices.rows} 行 · 有效收盘 {data.prices.valid_close_rows} · 异常 {data.prices.invalid_rows} · 重复组 {data.prices.duplicate_groups} · {data.prices.observed_dates} 个观测日</p>
      <ul className="list-inside list-disc">{data.factors.map((factor) => <li key={factor.factor_id}>{factor.label}：{factor.status === "potential_inputs" ? "仅存在候选源输入" : factor.reasons.map((reason) => reasonLabels[reason]).join("、")}（至少{factor.minimum_observed_dates}个观测日）</li>)}</ul>
      {data.auxiliary_missing.length > 0 && <p role="alert">市值中性化或加权缺少正有限总市值；未用0或1替代。</p>}
      {data.fields.length > 0 && <div className="overflow-auto"><table className="w-full whitespace-nowrap text-right"><thead><tr>{["依赖字段", "单位", "候选行", "有效值", "缺失", "非有限", "非正排除"].map((label) => <th className="p-2" key={label}>{label}</th>)}</tr></thead><tbody>{data.fields.map((field) => <tr key={field.field}><td className="p-2">{field.field}</td><td>{field.unit}</td><td>{field.source_rows}</td><td>{field.usable_values}</td><td>{field.missing_values}</td><td>{field.nonfinite_values}</td><td>{field.excluded_nonpositive}</td></tr>)}</tbody></table></div>}
      <p className="text-[var(--text-muted)]">估值行是证券×日期交集，财报行是结束日前候选披露记录（可早于开始日）；两者不是预处理后的日期×证券有效因子数，不证明整个股票池有完整输入。</p>
      {data.limitations.map((limitation) => <p key={limitation} className="text-[var(--text-muted)]">{limitation}</p>)}
      <p className="text-[var(--text-muted)]">读取时间 {data.read_at} · {data.inputs_version} · 仅候选值聚合，不是不可变数据版本；与容量预检独立读取。</p>
    </>}
  </section>
}
