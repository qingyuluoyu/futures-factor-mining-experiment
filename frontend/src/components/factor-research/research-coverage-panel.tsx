import type { ResearchCoverage } from "@/lib/stock-research-coverage"

export function ResearchCoveragePanel({ coverage, loading, error, onRefresh, onApply, disabled = false }: {
  coverage: ResearchCoverage | null; loading: boolean; error: string; onRefresh: () => void; onApply: () => void; disabled?: boolean
}): React.JSX.Element {
  return (
    <section aria-label="本地研究数据覆盖" className="mb-4 space-y-2 rounded-lg border border-[var(--border)] p-3 text-xs text-[var(--text-secondary)]">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="font-medium text-[var(--text-primary)]">本地研究数据覆盖</h3>
        <button type="button" onClick={onRefresh} disabled={loading} className="underline disabled:opacity-50">{error ? "重试覆盖预检" : "刷新覆盖预检"}</button>
        <button type="button" onClick={onApply} disabled={disabled || loading || !coverage?.recommended} className="underline disabled:opacity-50">使用最近覆盖区间</button>
      </div>
      {loading && <p role="status">正在预检当前日期范围…</p>}
      {error && <p role="alert" className="text-[var(--accent-danger)]">覆盖预检失败：{error}。请重试后再提交。</p>}
      {coverage && <>
        <p>证券目录 {coverage.catalog_count} 只 · 全部有价证券 {coverage.available.securities} 只 · 实际覆盖 {coverage.available.first_trade_date ?? "无"} 至 {coverage.available.last_trade_date ?? "无"}</p>
        <p>当前区间 {coverage.requested ? `${coverage.requested.start_date} 至 ${coverage.requested.end_date}` : "无"} · 有价证券 {coverage.selected.securities} 只 · {coverage.selected.observed_dates} 个观测日 · 价格 {coverage.selected.price_rows} 行 · 缺价格 {coverage.selected.missing_price_cells} · 计算面板 {coverage.selected.panel_cells}/{coverage.limits.max_panel_cells} 格</p>
        {"dependencies" in coverage && <p>按当前配置预检：价格 close · 估值 {coverage.dependencies.valuation_fields.join("、") || "不需要"} · 财报 {coverage.dependencies.financial_fields.join("、") || "不需要"}。未请求的字段不加载、不补值；中性化和市值加权会加入总市值依赖。</p>}
        {coverage.recommended && <p>最近覆盖区间 {coverage.recommended.start_date} 至 {coverage.recommended.end_date}，最多最近61个真实观测日。</p>}
        <p role={coverage.status === "within_limits" ? "status" : "alert"} className={coverage.status === "within_limits" ? "" : "text-[var(--accent-danger)]"}>
          {coverage.status === "no_data" ? "当前区间无价格数据，请调整日期。" : coverage.status === "over_limit" ? "当前区间超过计算限制，请缩短日期范围。" : "当前统计在容量限制内，不保证金融计算成功或策略有效。"}
        </p>
        {coverage.warnings.map((warning, index) => <p key={index}>{warning}</p>)}
      </>}
      <p className="text-[11px] text-[var(--text-muted)]">仅统计本地前复权日线覆盖，不下载数据；缺价、值合法性、交易日历、授权及历史时点（PIT）仍须实际研究检查。目录数量不代表完整历史。</p>
    </section>
  )
}
