"use client"

import type { ResearchPreprocess } from "@/lib/stock-research-api"

export function ResearchPreprocessControls({ value, onChange, disabled = false }: { value: ResearchPreprocess; onChange: (value: ResearchPreprocess) => void; disabled?: boolean }): React.JSX.Element {
  return <fieldset disabled={disabled} className="mb-4 space-y-3 rounded-lg border border-[var(--border)] p-3">
    <legend className="px-1 text-xs font-medium">预处理设置（检验与回测共用）</legend>
    <label className="flex items-center gap-3 text-xs">去极值方法
      <select aria-label="去极值方法" value={value.winsorize} onChange={(event) => { const method = event.target.value; if (!disabled && (method === "mad" || method === "3sigma" || method === "none")) onChange({ ...value, winsorize: method }) }} className="rounded border border-[var(--border)] bg-[var(--bg-secondary)] px-2 py-1">
        <option value="mad">MAD去极值</option><option value="3sigma">3σ去极值</option><option value="none">不去极值</option>
      </select>
    </label>
    <div className="flex flex-wrap gap-x-5 gap-y-3">
      {([{ key: "standardize", label: "Z-Score标准化" }, { key: "fillna", label: "缺失值处理" }, { key: "industry_neutral", label: "行业中性化" }, { key: "mktcap_neutral", label: "市值中性化" }] as const).map((field) => <label key={field.key} className="flex items-center gap-2 text-xs">
        <input type="checkbox" aria-label={field.label} checked={value[field.key]} onChange={(event) => { if (!disabled) onChange({ ...value, [field.key]: event.target.checked }) }} />{field.label}
      </label>)}
    </div>
    <p className="text-[11px] leading-5 text-[var(--text-muted)]">中性化需要行业/市值覆盖；缺少必要输入会明确失败。缺失值处理不补造缺失价格，也不表示历史分类和财报修订已认证。</p>
  </fieldset>
}
