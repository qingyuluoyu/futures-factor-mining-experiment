"use client"

/** 创建量化任务：策略选择与参数区 */

import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { StrengthEntryParams } from "@/components/backtest/quant-params-strength"
import { StrengthEntryV2Params } from "@/components/backtest/quant-params-strength-v2"
import {
  QUANT_KIND_OPTIONS,
  type QuantKind,
  type QuantParamsState,
} from "@/lib/quant-strategy"
import { FactorKindParams } from "./factor-kind-params"

interface CreateQuantParamsProps {
  quant: QuantParamsState
  onQuant: (q: QuantParamsState) => void
  symbol: string
  /** 选中收藏因子时回填品种/周期（仅 factor 策略触发） */
  onApplyFactorMeta?: (symbol: string | null, timeframe: string | null) => void
}

/** 策略类型按钮 + 参数输入 */
export function CreateQuantParams({
  quant,
  onQuant,
  symbol,
  onApplyFactorMeta,
}: CreateQuantParamsProps): React.JSX.Element {
  return (
    <>
      <div className="space-y-1">
        <Label>策略类型</Label>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {QUANT_KIND_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              disabled={opt.disabled === true}
              title={opt.disabled === true ? "该策略暂停使用" : undefined}
              className={`h-9 rounded-md border text-xs transition-colors ${
                opt.disabled === true
                  ? "cursor-not-allowed border-[var(--border)] text-[var(--text-muted)] opacity-50"
                  : quant.quantKind === opt.value
                    ? "border-[var(--primary)] bg-[var(--primary)]/15 text-[var(--primary)]"
                    : "border-[var(--border)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]"
              }`}
              onClick={() => onQuant({ ...quant, quantKind: opt.value })}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>
      <div className="rounded-md border border-[var(--border)] p-2.5 space-y-2">
        <div className="text-xs font-medium text-[var(--text-secondary)]">
          策略参数
        </div>
        <KindParams
          quant={quant}
          onQuant={onQuant}
          symbol={symbol}
          onApplyFactorMeta={onApplyFactorMeta}
        />
        <p className="text-[10px] text-[var(--text-muted)] leading-relaxed">
          {hintFor(quant.quantKind)}
        </p>
      </div>
    </>
  )
}

export function KindParams({
  quant,
  onQuant,
  symbol,
  onApplyFactorMeta,
}: CreateQuantParamsProps): React.JSX.Element {
  if (quant.quantKind === "n_breakout") {
    return (
      <Field
        label="回看周期 N"
        type="number"
        min={2}
        value={quant.lookback}
        onChange={(v) => onQuant({ ...quant, lookback: Math.max(2, v) })}
      />
    )
  }
  if (quant.quantKind === "ma_cross") {
    return (
      <div className="grid grid-cols-2 gap-2">
        <Field
          label="快线"
          type="number"
          min={1}
          value={quant.fastPeriod}
          onChange={(v) => onQuant({ ...quant, fastPeriod: Math.max(1, v) })}
        />
        <Field
          label="慢线"
          type="number"
          min={2}
          value={quant.slowPeriod}
          onChange={(v) => onQuant({ ...quant, slowPeriod: Math.max(2, v) })}
        />
      </div>
    )
  }
  if (quant.quantKind === "macd_cross") {
    return (
      <div className="grid grid-cols-3 gap-2">
        <Field
          label="快"
          type="number"
          min={2}
          value={quant.macdFast}
          onChange={(v) => onQuant({ ...quant, macdFast: Math.max(2, v) })}
        />
        <Field
          label="慢"
          type="number"
          min={3}
          value={quant.macdSlow}
          onChange={(v) => onQuant({ ...quant, macdSlow: Math.max(3, v) })}
        />
        <Field
          label="信号"
          type="number"
          min={2}
          value={quant.macdSignal}
          onChange={(v) => onQuant({ ...quant, macdSignal: Math.max(2, v) })}
        />
      </div>
    )
  }
  if (quant.quantKind === "kdj_cross") {
    return (
      <div className="space-y-2">
        <div className="grid grid-cols-3 gap-2">
          <Field
            label="N"
            type="number"
            min={2}
            value={quant.kdjN}
            onChange={(v) => onQuant({ ...quant, kdjN: Math.max(2, v) })}
          />
          <Field
            label="K"
            type="number"
            min={2}
            value={quant.kdjK}
            onChange={(v) => onQuant({ ...quant, kdjK: Math.max(2, v) })}
          />
          <Field
            label="D"
            type="number"
            min={2}
            value={quant.kdjD}
            onChange={(v) => onQuant({ ...quant, kdjD: Math.max(2, v) })}
          />
        </div>
        <label className="flex items-center gap-2 text-[11px] text-[var(--text-secondary)]">
          <input
            type="checkbox"
            checked={quant.kdjUseZone}
            onChange={(e) =>
              onQuant({ ...quant, kdjUseZone: e.target.checked })
            }
          />
          仅超卖金叉 / 超买死叉
        </label>
      </div>
    )
  }
  if (quant.quantKind === "strength_entry") {
    return <StrengthEntryParams quant={quant} onQuant={onQuant} />
  }
  if (quant.quantKind === "strength_entry_v2") {
    return <StrengthEntryV2Params quant={quant} onQuant={onQuant} />
  }
  if (quant.quantKind === "factor") {
    return (
      <FactorKindParams
        quant={quant}
        onQuant={onQuant}
        symbol={symbol}
        onApplyMeta={onApplyFactorMeta}
      />
    )
  }
  return (
    <div className="grid grid-cols-2 gap-2">
      <Field
        label="周期"
        type="number"
        min={5}
        value={quant.bandPeriod}
        onChange={(v) => onQuant({ ...quant, bandPeriod: Math.max(5, v) })}
      />
      <div className="space-y-1">
        <Label className="text-[11px]">标准差</Label>
        <Input
          type="number"
          min={0.5}
          step={0.1}
          value={quant.bandStd}
          onChange={(e) =>
            onQuant({
              ...quant,
              bandStd: Math.max(0.5, Number(e.target.value) || 2),
            })
          }
        />
      </div>
    </div>
  )
}

function Field({
  label,
  type,
  min,
  value,
  onChange,
}: {
  label: string
  type: "number"
  min: number
  value: number
  onChange: (v: number) => void
}): React.JSX.Element {
  return (
    <div className="space-y-1">
      <Label className="text-[11px]">{label}</Label>
      <Input
        type={type}
        min={min}
        value={value}
        onChange={(e) => onChange(Number(e.target.value) || min)}
      />
    </div>
  )
}

function hintFor(kind: QuantKind): string {
  if (kind === "n_breakout") {
    return "收盘突破前 N 根最高 → 买多；跌破最低 → 卖空。反向持仓先平。"
  }
  if (kind === "ma_cross") {
    return "金叉（快线上穿慢线）→ 买多；死叉 → 卖空。"
  }
  if (kind === "macd_cross") {
    return "DIF 上穿 DEA → 买多；下穿 → 卖空。默认 12/26/9。"
  }
  if (kind === "kdj_cross") {
    return "K 上穿 D → 买多；下穿 → 卖空。可选仅在超买超卖区生效。"
  }
  if (kind === "strength_entry") {
    return "0-100 强弱指标三档进场（只做多）：波段=顺势上穿；反弹=弱区回升；超跌=极低位拐头。强弱值跌破平多阈值离场；不出空信号。参数与图表强弱副图同口径。"
  }
  if (kind === "strength_entry_v2") {
    return "0-100 强弱形态 V2 六档双向：底部/顶部衰竭=段谷/峰动能枯竭预警；反弹/破位=regime 翻转确认；中继=回调不破位再顺势。反向信号或 regime 翻转平仓。参数与图表 V2 副图同口径。"
  }
  if (kind === "factor") {
    return "优先从收藏下拉选择；也可粘贴因子实验室 tokens。公式 → tanh 仓位意图。"
  }
  return "触及下轨 → 买多；触及上轨 → 卖空；回到中轨 → 平仓。"
}
