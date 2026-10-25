"use client"

import { MiniLineChart } from "@/components/factor-research/mini-line-chart"
import type { FactorAnalysisResult, BacktestResult } from "@/lib/stock-api"
import type { ResearchJob, ResearchResult } from "@/lib/stock-research-api"
import { cn } from "@/lib/utils"

export function ResearchReport({ job, result }: { job: ResearchJob; result: ResearchResult }): React.JSX.Element {
  const analysis = job.kind === "analysis" ? result as unknown as FactorAnalysisResult : null
  const backtest = job.kind === "backtest" ? result as unknown as BacktestResult : null
  const horizon = Number(job.params?.horizon ?? 20)
  const groups = Number(job.params?.groups)
  const groupLabel = Number.isInteger(groups) && groups >= 2 && groups <= 10 ? `分 ${groups} 组` : "分组数量未记录"
  const preprocess = job.params?.preprocess as Record<string, unknown> | undefined
  const knownV5 = result.method_version === "stock-research-2026-10-19.v5"
  const detail = result as ResearchResult & { trades_total?: number; trades_truncated?: boolean }
  const stateLabel = (value: unknown) => value === true ? "开" : value === false ? "关" : "未记录"
  return <section className="space-y-4">
    <p className="break-all text-xs text-[var(--text-muted)]">报告 {job.id} · 数据版本 {result.data_version} · 流程版本 {job.workflow_version}</p>
    <p className="text-xs text-[var(--text-muted)]">数据指纹仅标识本次计算输入，不是不可变原始数据归档。证券当前分类、历史修订及真实成交条件仍待验证；仅用于研究。</p>
    <details className="text-xs"><summary>数据来源与覆盖</summary><pre className="overflow-auto whitespace-pre-wrap">{JSON.stringify(result.data_manifest, null, 2)}</pre></details>
    <details className="text-xs"><summary>已保存的研究参数（不随当前表单修改）</summary><pre className="overflow-auto whitespace-pre-wrap">{JSON.stringify(job.params ?? {}, null, 2)}</pre></details>
    {result.neutralization_policy ? <p className="text-xs text-[var(--text-muted)]">中性化政策：{result.neutralization_policy.version}；如启用相应中性化，行业组至少{result.neutralization_policy.min_industry_observations}个有效样本，设计矩阵满列秩，残差自由度至少{result.neutralization_policy.min_residual_degrees_of_freedom}；不足保持缺失，不填零或重新填补。</p> : <p className="text-xs text-[var(--text-muted)]">中性化政策：未记录（旧报告保留原结果）</p>}
    {result.factor_policies?.length ? <details className="text-xs"><summary>因子样本政策与计数（日期×证券）</summary>{result.factor_policies.map((policy) => <div key={policy.factor_id} className="mt-2 space-y-1">
      <p>{policy.factor_id} · {policy.policy_version} · {policy.start_date} 至 {policy.end_date}</p>
      <p>{policy.exclusion_rule}</p>
      <p>{policy.missing_value_policy === "exclude_without_imputation" ? "金融域不合格样本不填补" : "通用缺失值按保存的预处理配置填补"}</p>
      <p>总观测{policy.counts.observations} · 原始合格{policy.counts.raw_eligible} · 非正{policy.counts.excluded_nonpositive} · 非有限{policy.counts.excluded_nonfinite} · 缺失{policy.counts.missing} · 预处理后{policy.counts.postprocess_eligible} · 填补{policy.counts.imputed}</p>
      <p>计数为输入区间的日期×证券观测数；实际IC、分组和选股还受可比较收益、分组数量与执行日价格约束。</p>
    </div>)}</details> : <p className="text-xs text-[var(--text-muted)]">因子样本政策：未记录（旧报告保留原结果）</p>}
    {preprocess && <p className="text-xs text-[var(--text-muted)]">{preprocess.winsorize === "mad" ? "MAD去极值" : preprocess.winsorize === "3sigma" ? "3σ去极值" : preprocess.winsorize === "none" ? "不去极值" : "去极值未记录"} · 标准化{stateLabel(preprocess.standardize)} · 缺失值处理{stateLabel(preprocess.fillna)} · 行业中性{stateLabel(preprocess.industry_neutral)} · 市值中性{stateLabel(preprocess.mktcap_neutral)}</p>}
            {analysis?.ic && (
              <>
                <div className="rounded-md border border-[var(--border)] bg-[var(--bg-secondary)] p-3 text-xs leading-relaxed text-[var(--text-muted)]">
                  <p>方法版本：{analysis.method_version ?? "旧版（未提供版本）"} · 状态：待验证的研究结果</p>
                  {(analysis.validation_limits ?? ["历史数据版本与真实成交条件未完整验证。"]).map((limit) => <p key={limit}>{limit}</p>)}
                  <p>信号在下一观测交易日收盘假设执行；多空曲线为每日调仓、零成本、名义多头1与空头1的理论序列，借券与成本未验证。未来收益有重叠，IC年化IR仅为描述统计。</p>
                </div>
                
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  {(
                    [
                      { label: "IC 均值", value: analysis.ic.ic_mean },
                      { label: "IC IR（年化）", value: analysis.ic.ic_ir ?? "—" },
                      { label: "秩IC 均值", value: analysis.ic.rank_ic_mean },
                      { label: "IC>0 占比", value: `${(analysis.ic.ic_positive_ratio * 100).toFixed(1)}%` },
                    ] as const
                  ).map((card) => (
                    <div
                      key={card.label}
                      className="rounded-lg border border-[var(--border)] bg-[var(--bg-secondary)] p-3"
                    >
                      <p className="text-[11px] text-[var(--text-muted)]">{card.label}</p>
                      <p className="font-num text-lg font-semibold text-[var(--text-primary)]">
                        {card.value}
                      </p>
                    </div>
                  ))}
                </div>

                
                <div className="grid gap-4 lg:grid-cols-2">
                  <div>
                    <p className="mb-2 text-xs font-medium text-[var(--text-primary)]">
                      IC 衰减曲线（秩IC vs 预测周期）
                    </p>
                    <MiniLineChart
                      points={analysis.decay
                        .filter((d) => d.rank_ic != null)
                        .map((d) => ({ x: d.lag * 86400, y: d.rank_ic as number }))}
                      color="#f5a623"
                    />
                  </div>
                  <div>
                    <p className="mb-2 text-xs font-medium text-[var(--text-primary)]">
                      理论多空净值（每日调仓、零成本）
                    </p>
                    <MiniLineChart
                      points={analysis.long_short_nav.map((p) => ({ x: p.date, y: p.nav }))}
                      color="#22c55e"
                      valueFormat={(v) => v.toFixed(3)}
                    />
                  </div>
                </div>

                
                <div>
                  <p className="mb-2 text-xs font-medium text-[var(--text-primary)]">
                    分组平均收益（因子值{groupLabel}，未来 {horizon} 日）
                  </p>
                  <div className="relative flex h-48 gap-3 rounded-lg border border-[var(--border)] bg-[var(--bg-secondary)] px-4">
                    <div aria-label="分组收益零轴" className="absolute inset-x-4 top-1/2 border-t border-[var(--text-muted)]" />
                    {analysis.quantile_returns.map((q) => {
                      const max = Math.max(
                        ...analysis.quantile_returns.map((x) => Math.abs(x.avg_return)),
                        0.0001,
                      )
                      const pct = (Math.abs(q.avg_return) / max) * 100
                      const positive = q.avg_return >= 0
                      return (
                        <div key={q.group} className="relative h-full flex-1">
                          <span className="absolute inset-x-0 top-1 text-center font-num text-[11px] text-[var(--text-secondary)]">
                            {(q.avg_return * 100).toFixed(2)}%
                          </span>
                          <div
                            className={cn(
                              "absolute inset-x-0 mx-auto w-full max-w-16",
                              positive ? "bg-[var(--accent-up)]/70" : "bg-[var(--accent-down)]/70",
                            )}
                            data-return-sign={q.avg_return === 0 ? "zero" : positive ? "positive" : "negative"}
                            style={{ height: `${pct * 0.4}%`, ...(positive ? { bottom: "50%" } : { top: "50%" }) }}
                          />
                          <span className="absolute inset-x-0 bottom-1 text-center text-[10px] text-[var(--text-muted)]">
                            Q{q.group + 1}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                </div>
              </>
            )}

            {backtest?.metrics && (
              <>
                <div className="rounded-md border border-[var(--border)] bg-[var(--bg-secondary)] p-3 text-xs leading-relaxed text-[var(--text-muted)]">
                  <p>方法版本：{backtest.method_version ?? "旧版（未提供版本）"} · 状态：待验证的研究结果</p>
                  {(backtest.validation_limits ?? ["历史数据版本与真实成交条件未完整验证。"]).map((limit) => <p key={limit}>{limit}</p>)}
                  <p>使用前一交易日信号，在当日收盘假设执行；持仓份额保持至下次调仓，逐日按观测价估值。手续费与滑点按实际买卖金额从组合资产扣除。</p>
                  {knownV5 ? <p>统计口径：244个观测交易日年化，无风险收益默认0；年化波动使用样本标准差，旧版夏普使用总体标准差。正收益日占比不是逐笔交易胜率；平均调仓换手比例不是年化换手。保留已保存数值，不用新口径改写旧报告。</p> : <p>统计口径：未识别；请以该报告保存的方法版本与参数为准，不补写当前方法假设。</p>}
                </div>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-8">
                  {(
                    [
                      { label: "总收益", value: `${backtest.metrics.total_return_pct.toFixed(2)}%` },
                      { label: "年化收益", value: `${backtest.metrics.annualized_return_pct.toFixed(2)}%` },
                      { label: "年化波动", value: `${backtest.metrics.annualized_vol_pct.toFixed(2)}%` },
                      { label: "夏普", value: backtest.metrics.sharpe },
                      { label: "卡玛", value: backtest.metrics.calmar ?? "—" },
                      { label: "最大回撤", value: `${backtest.metrics.max_drawdown_pct.toFixed(2)}%` },
                      { label: knownV5 ? "正收益日占比" : "胜率（历史口径）", value: `${backtest.metrics.win_rate.toFixed(1)}%` },
                      { label: knownV5 ? "平均调仓换手比例" : "换手率（历史口径）", value: backtest.metrics.turnover_rate?.toFixed(3) ?? "—" },
                    ] as const
                  ).map((card) => (
                    <div
                      key={card.label}
                      className="rounded-lg border border-[var(--border)] bg-[var(--bg-secondary)] p-3"
                    >
                      <p className="text-[11px] text-[var(--text-muted)]">{card.label}</p>
                      <p className="font-num text-base font-semibold text-[var(--text-primary)]">
                        {card.value}
                      </p>
                    </div>
                  ))}
                </div>

                <div>
                  <p className="mb-2 text-xs font-medium text-[var(--text-primary)]">
                    策略净值曲线（{backtest.rebalance_count} 次调仓）
                  </p>
                  <MiniLineChart
                    points={backtest.nav.map((p) => ({ x: p.date, y: p.nav }))}
                    color="#3b82f6"
                    height={260}
                    valueFormat={(v) => v.toFixed(3)}
                  />
                </div>

                
                <div>
                  <p className="mb-2 text-xs font-medium text-[var(--text-primary)]">
                    调仓明细（最近 {Math.min(backtest.trades.length, 60)} 条）
                  </p>
                  {Number.isSafeInteger(detail.trades_total) && detail.trades_total! >= backtest.trades.length && <p className="mb-2 text-xs text-[var(--text-muted)]">实际共{detail.trades_total}条；本报告载入{backtest.trades.length}条，页面显示最近{Math.min(backtest.trades.length, 60)}条。{detail.trades_truncated ? "保存明细已截断，不能将当前展示或导出当作完整交易流水。" : ""}</p>}
                  <div className="max-h-72 overflow-y-auto rounded-lg border border-[var(--border)]">
                    <table className="w-full text-xs">
                      <thead className="sticky top-0 bg-[var(--bg-tertiary)] text-[var(--text-muted)]">
                        <tr>
                          <th className="px-3 py-2 text-left font-medium">日期</th>
                          <th className="px-3 py-2 text-left font-medium">代码</th>
                          <th className="px-3 py-2 text-left font-medium">名称</th>
                          <th className="px-3 py-2 text-right font-medium">权重</th>
                          <th className="px-3 py-2 text-left font-medium">操作</th>
                          <th className="px-3 py-2 text-right font-medium">因子值</th>
                        </tr>
                      </thead>
                      <tbody>
                        {backtest.trades.slice(-60).map((t, i) => (
                          <tr key={`${t.date}-${t.code}-${i}`} className="border-t border-[var(--border)]">
                            <td className="px-3 py-1.5 font-num text-[var(--text-secondary)]">{t.date}</td>
                            <td className="px-3 py-1.5 font-num text-[var(--text-primary)]">{t.code}</td>
                            <td className="px-3 py-1.5 text-[var(--text-primary)]">{t.name}</td>
                            <td className="px-3 py-1.5 text-right font-num text-[var(--text-secondary)]">
                              {(t.weight * 100).toFixed(1)}%
                            </td>
                            <td className="px-3 py-1.5 text-[var(--accent-up)]">{t.action}</td>
                            <td className="px-3 py-1.5 text-right font-num text-[var(--text-muted)]">
                              {t.factor_value ?? "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </>
            )}


  </section>
}

