/** Stock-only, read-only navigation. Industry has the same bound as the members API. */
export function parseStockIndustry(value: string | null): { value: string; error?: never } | { value?: never; error: string } {
  const industry = value?.trim() ?? ""
  if (Array.from(industry).length > 32 || /[\u0000-\u001f\u007f]/.test(industry)) {
    return { error: "行业范围无效，请使用不超过32个字符的行业名称。" }
  }
  return { value: industry }
}

export function stockMarketHref(industry = "", code?: string): string {
  const parsed = parseStockIndustry(industry)
  if (parsed.error) throw new RangeError(parsed.error)
  if (code !== undefined && !/^\d{6}$/.test(code)) throw new RangeError("股票代码必须为六位数字")
  const query = new URLSearchParams()
  if (parsed.value) query.set("industry", parsed.value)
  if (code) query.set("symbol", code)
  return `/market${query.size ? `?${query}` : ""}`
}
