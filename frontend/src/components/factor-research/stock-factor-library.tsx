"use client"

import Link from "next/link"
import { useEffect, useState } from "react"
import { getStockFactorsApi, type StockFactorSpec } from "@/lib/stock-api"
import { StockFactorEditor } from "@/components/factor-research/stock-factor-editor"

export function StockFactorLibrary({ token, scope, title = "A股因子实验室" }: { token: string; scope: string; title?: string }): React.JSX.Element {
  const [catalog, setCatalog] = useState<StockFactorSpec[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let active = true
    setLoading(true)
    setError("")
    void getStockFactorsApi().then((rows) => { if (active) setCatalog(rows) }, (failure) => {
      if (active) { setCatalog([]); setError(failure instanceof Error ? failure.message : "因子目录读取失败") }
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [token, scope, revision])

  return <div className="space-y-5 p-4">
    <header className="space-y-2">
      <h1 className="text-lg font-semibold">{title}</h1>
      <p className="text-xs leading-6 text-[var(--text-muted)]">查看内置因子的公式、方向与数据要求，管理自定义定义及不可变历史版本。研究结果需另行进行数据覆盖检查与检验。</p>
      <nav aria-label="因子研究流程" className="flex gap-4 text-xs"><Link href="/factor-research" className="underline">进入因子检验</Link><Link href="/backtest" className="underline">进入组合回测</Link><Link href="/history" className="underline">查看历史研究报告</Link></nav>
    </header>
    <section aria-label="内置因子目录" className="space-y-3">
      <h2 className="text-sm font-semibold">内置因子目录</h2>
      {loading && <p role="status" className="text-xs">因子目录加载中…</p>}
      {error && <div role="alert" className="space-y-2 text-xs"><p>{error}</p><button type="button" className="underline" onClick={() => setRevision((value) => value + 1)}>重试因子目录</button></div>}
      {!loading && !error && !catalog.length && <p role="status" className="text-xs">暂无内置因子。自定义定义管理仍按服务能力开放。</p>}
      {!loading && !error && <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{catalog.map((factor) => <article key={factor.id} className="space-y-2 rounded-lg border border-[var(--border)] bg-[var(--bg-secondary)] p-3 text-xs">
        <h3 className="font-semibold">{factor.label} <span className="font-normal text-[var(--text-muted)]">{factor.group}</span></h3>
        <p>{factor.direction === "high" ? "因子越大越好" : "因子越小越好"} · <code>{factor.id}</code></p>
        <p className="break-words font-mono">{factor.formula}</p>
        <p className="leading-6">{factor.description}</p>
        <p>数据来源：{factor.source}</p>
        <p>方法版本：{factor.method_version ?? "来源未提供"}</p>
        {factor.eligibility_rule && <p>资格规则：{factor.eligibility_rule}</p>}
        {factor.missing_value_policy && <p>缺失处理：{factor.missing_value_policy}</p>}
        {factor.exclusion_rule && <p>排除规则：{factor.exclusion_rule}</p>}
        {factor.policy_version && <p>规则版本：{factor.policy_version}</p>}
      </article>)}</div>}
    </section>
    <StockFactorEditor token={token} scope={scope} />
  </div>
}
