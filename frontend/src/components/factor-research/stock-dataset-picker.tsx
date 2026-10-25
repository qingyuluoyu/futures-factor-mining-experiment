"use client"

import { useEffect, useRef, useState } from "react"
import { getStockDatasetAvailability, getStockDatasetDescription, listStockDatasets, type DatasetAvailability, type DatasetCatalogItem, type DatasetCatalogPage, type DatasetDescription, type DatasetRef } from "@/lib/stock-dataset-api"

type Identity = { token: string; scope: string }
type State = {
  identity: Identity; availability: DatasetAvailability | null; page: DatasetCatalogPage | null; offset: number
  loading: boolean; error: string; selected: DatasetRef | null; detail: DatasetDescription | null; detailLoading: boolean; detailError: string
}
const LIMIT = 20
const buttonClass = "cursor-pointer rounded border border-[var(--border)] px-2 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-50"
const kindLabel = { daily: "日线", valuation: "估值", financial: "财务披露" }
function initial(identity: Identity): State { return { identity, availability: null, page: null, offset: 0, loading: true, error: "", selected: null, detail: null, detailLoading: false, detailError: "" } }
function message(error: unknown) { return error instanceof Error ? error.message.slice(0, 400) : "固定数据目录请求失败" }

export function StockDatasetPicker({ token, scope }: { token: string; scope: string }): React.JSX.Element {
  const identity = useRef<Identity>({ token, scope })
  if (identity.current.token !== token || identity.current.scope !== scope) identity.current = { token, scope }
  const [state, setState] = useState<State>(() => initial(identity.current))
  const listing = useRef<AbortController | null>(null)
  const detailRequest = useRef<AbortController | null>(null)

  function patch(origin: Identity, values: Partial<State>) {
    if (identity.current !== origin) return
    setState((previous) => previous.identity === origin ? { ...previous, ...values } : previous)
  }

  function load(offset = 0) {
    listing.current?.abort()
    const controller = new AbortController()
    listing.current = controller
    const origin = identity.current
    patch(origin, { loading: true, error: "" })
    void (async () => {
      try {
        const availability = await getStockDatasetAvailability(origin.token, controller.signal)
        if (controller.signal.aborted || listing.current !== controller) return
        patch(origin, { availability })
        if (availability.catalog_enabled) {
          const page = await listStockDatasets({ limit: LIMIT, offset }, origin.token, controller.signal)
          if (!controller.signal.aborted && listing.current === controller) patch(origin, { page, offset })
        }
      } catch (error) {
        if (!controller.signal.aborted && listing.current === controller) patch(origin, { error: message(error) })
      } finally {
        if (listing.current === controller) { listing.current = null; patch(origin, { loading: false }) }
      }
    })()
  }

  function inspect(item: DatasetCatalogItem) {
    if (!state.availability?.detail_enabled || item.artifact_state !== "ready" || state.loading || state.error) return
    detailRequest.current?.abort()
    const controller = new AbortController()
    detailRequest.current = controller
    const origin = identity.current
    const selected: DatasetRef = { dataset_id: item.dataset_id, data_version: item.data_version, qualification: item.qualification }
    patch(origin, { selected, detail: null, detailLoading: true, detailError: "" })
    void (async () => {
      try {
        const detail = await getStockDatasetDescription(selected, origin.token, controller.signal)
        if (!controller.signal.aborted && detailRequest.current === controller) patch(origin, { detail })
      } catch (error) {
        if (!controller.signal.aborted && detailRequest.current === controller) patch(origin, { detailError: message(error) })
      } finally {
        if (detailRequest.current === controller) { detailRequest.current = null; patch(origin, { detailLoading: false }) }
      }
    })()
  }

  useEffect(() => {
    const origin = identity.current
    setState(initial(origin))
    load()
    return () => { listing.current?.abort(); detailRequest.current?.abort(); listing.current = null; detailRequest.current = null }
  }, [token, scope])

  if (state.identity !== identity.current) return <p role="status">正在清理旧会话的数据集信息…</p>
  return <section aria-label="A股固定数据版本只读查看" className="space-y-3 rounded-lg border border-[var(--border)] p-3 text-xs">
    <h2 className="font-semibold">固定数据版本 · 只读诊断</h2>
    <p className="leading-6 text-[var(--text-muted)]">清单声明不是实际行情覆盖，登记记录也不是数据真实性认证。本入口只查看本人当前登录盘的版本，不导入数据、不调用模型、不创建研究任务。</p>
    <button type="button" className={buttonClass} disabled={state.loading} onClick={() => load(state.offset)}>刷新数据目录</button>
    {state.loading && <p role="status">正在读取数据目录…</p>}
    {state.error && <p role="alert" className="text-[var(--accent-danger)]">{state.error}。没有用空列表代替失败；如保留了旧条目，它们仅供本会话核对。</p>}
    {state.availability && <p role="status">{state.availability.reason}</p>}
    {state.availability?.catalog_enabled && state.page && <>
      <p>已登记 {state.page.total} 个版本；本页 {state.page.items.length} 个，每页最多 {LIMIT} 个。列表尚未逐个检查源文件。</p>
      {!state.loading && !state.error && state.page.items.length === 0 && <p role="status">尚无已登记固定版本；未生成假数据，也未自动补采。</p>}
      <ul className="grid gap-2 md:grid-cols-2">{state.page.items.map((item) => <li key={item.dataset_id} className="space-y-1 rounded border border-[var(--border)] p-2">
        <p className="break-all">{item.dataset_id}</p><p>数据版本 <code title={item.data_version}>{item.data_version.slice(0, 16)}…</code></p>
        <p>{item.qualification === "observation_only" ? "观察数据（不能用于回测）" : "资格摘要仍须服务端重验"} · {item.artifact_state === "ready" ? "登记就绪，源文件未核验" : "产物已隔离，禁止读取"}</p>
        <p>登记时间 {item.created_at}</p>
        <button type="button" className={buttonClass} disabled={state.loading || Boolean(state.error) || !state.availability?.detail_enabled || item.artifact_state !== "ready"} onClick={() => inspect(item)}>查看固定版本 {item.data_version.slice(0, 8)}</button>
      </li>)}</ul>
      {!state.availability.detail_enabled && <p>当前未配置可读产物根，详情关闭；未自动创建目录或换用其他数据。</p>}
      <div className="flex gap-2"><button type="button" className={buttonClass} disabled={state.loading || state.offset === 0} onClick={() => load(Math.max(0, state.offset - LIMIT))}>目录上一页</button><button type="button" className={buttonClass} disabled={state.loading || !state.page.has_more} onClick={() => load(state.offset + LIMIT)}>目录下一页</button></div>
    </>}
    {state.selected && <div className="space-y-3 border-t border-[var(--border)] pt-3">
      <h3 className="font-medium">明确选中的固定引用</h3>
      <p className="break-all">数据集 {state.selected.dataset_id}</p><p className="break-all">版本 <code>{state.selected.data_version}</code></p>
      <p>刷新目录不会自动改成最新版本；需要重新点击条目才能更换引用。</p>
      {state.detailLoading && <p role="status">正在核对固定清单…</p>}
      {state.detailError && <p role="alert" className="text-[var(--accent-danger)]">{state.detailError}；没有用最新版本替代。</p>}
      {state.detail && <>
        <dl className="grid gap-2 md:grid-cols-2">
          <div><dt>清单声明区间</dt><dd>{state.detail.start} 至 {state.detail.end}，不是实际行情覆盖。</dd></div>
          <div><dt>暖机起点</dt><dd>{state.detail.warmup_start}</dd></div>
          <div><dt>固定股票池</dt><dd>{state.detail.pool_id} · 声明证券 {state.detail.instruments.length} 只</dd></div>
          <div><dt>来源声明记录数</dt><dd>{state.detail.sources.reduce((total, source) => total + source.row_count, 0)} 条（不是有效交易日×股票覆盖数）</dd></div>
        </dl>
        <p className="break-all">股票池版本 <code>{state.detail.pool_version}</code></p>
        <p>仅清单及登记关系已核对，源价格文件未核验；实际覆盖、公告时点、日历与交易资格尚未确认。</p>
        <ul className="list-inside list-disc space-y-1">{state.detail.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
        <p>证券展示 {Math.min(30, state.detail.instruments.length)} / {state.detail.instruments.length}：{state.detail.instruments.slice(0, 30).join("、")}</p>
        <h4>来源引用（最多展示20条，完整声明共 {state.detail.sources.length} 条）</h4>
        <ul className="space-y-2">{state.detail.sources.slice(0, 20).map((source) => <li key={source.source_id} className="rounded border border-[var(--border)] p-2"><p>{kindLabel[source.kind]} · 声明 {source.row_count} 条 · {source.byte_size} 字节</p><p className="break-all">来源ID {source.source_id}</p><p className="break-all"><code>{source.sha256}</code></p></li>)}</ul>
      </>}
      <button type="button" className={buttonClass} disabled>用于IC/回测（暂未开放）</button>
    </div>}
  </section>
}
