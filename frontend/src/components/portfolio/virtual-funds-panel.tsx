"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { Input } from "@/components/ui/input"
import { Wallet, Gift, RefreshCw, Loader2 } from "lucide-react"
import {
  depositPaperFunds,
  preparePaperDeposit,
  isDefinitivePaperDepositError,
  getPaperAccount,
  getPaperLedgers,
  type PaperAccountSummary,
  type PaperLedgerItem,
  type PendingPaperDeposit,
} from "@/lib/paper-api"
import { formatShanghaiTime } from "@/lib/utils"
import { useAuthStore } from "@/stores/auth"
import { paperValuationView } from "@/lib/paper-valuation"

/** 金额格式化 */
function formatMoney(value: number): string {
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

/** 流水类型中文 */
function flowTypeLabel(type: string): string {
  const map: Record<string, string> = {
    claim: "领取",
    deposit: "模拟充值",
    freeze: "冻结",
    unfreeze: "解冻",
    fee: "手续费",
    open: "开仓",
    close: "平仓盈亏",
    settlement: "结算",
  }
  return map[type] ?? type
}

export function VirtualFundsPanel(): React.JSX.Element {
  const token = useAuthStore((state) => state.accessToken)
  const userId = useAuthStore((state) => state.user?.id)
  const mode = useAuthStore((state) => state.user?.trading_mode)
  const revision = useAuthStore((state) => state.sessionRevision)
  const market = useAuthStore((state) => state.user?.market_type)
  const pending = useAuthStore((state) => state.marketSwitchPending)
  if (!token || !userId) return <p className="text-sm text-[var(--text-muted)]">请登录后查看模拟资金</p>
  if (pending) return <p role="status" className="text-sm text-[var(--text-muted)]">正在确认市场切换，请稍候…</p>
  if (market !== "futures") return <p className="text-sm text-[var(--text-muted)]">A股请使用独立日线回放账户</p>
  return <SessionFundsPanel key={`${revision}:${userId}:${mode}:${market}:${token}`} token={token} storageKey={`paper-pending-deposit:${userId}:${mode ?? "live"}`} />
}

function SessionFundsPanel({ token, storageKey }: { token: string; storageKey: string }): React.JSX.Element {
  const [account, setAccount] = useState<PaperAccountSummary | null>(null)
  const [ledgers, setLedgers] = useState<PaperLedgerItem[]>([])
  const [loading, setLoading] = useState(true)
  const [depositing, setDepositing] = useState(false)
  const [amount, setAmount] = useState("1000000.00")
  const [pending, setPending] = useState<PendingPaperDeposit | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [storageError, setStorageError] = useState<string | null>(null)
  const pendingRef = useRef<PendingPaperDeposit | null>(null)
  const active = useRef(true)
  const busy = useRef(false)
  const generation = useRef(0)
  const valuation = paperValuationView(account)

  const load = useCallback(async () => {
    const revision = ++generation.current
    setLoading(true)
    setError(null)
    try {
      const [acc, flow] = await Promise.all([
        getPaperAccount(token),
        getPaperLedgers(10, 0, token),
      ])
      if (!active.current || revision !== generation.current) return
      setAccount(acc)
      setLedgers(flow.items)
    } catch (err) {
      if (!active.current || revision !== generation.current) return
      setError(err instanceof Error ? err.message : "加载失败")
    } finally {
      if (active.current && revision === generation.current) setLoading(false)
    }
  }, [token])

  useEffect(() => {
    active.current = true
    try {
      const saved = sessionStorage.getItem(storageKey)
      if (saved) {
        const body = JSON.parse(saved) as PendingPaperDeposit["body"]
        if (typeof body.amount !== "string" || typeof body.idempotency_key !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(body.idempotency_key)) throw new Error("待确认充值记录损坏，请联系管理员核对流水")
        const restored = preparePaperDeposit({ sessionToken: token, body }, body.amount, token)
        pendingRef.current = restored
        setPending(restored)
        setAmount(restored.body.amount)
      }
    } catch (err) {
      setStorageError(err instanceof Error ? err.message : "待确认充值读取失败")
    }
    void load()
    return () => { active.current = false }
  }, [load, storageKey, token])

  const handleDeposit = async () => {
    if (busy.current || storageError) return
    busy.current = true
    generation.current += 1
    setLoading(false)
    setDepositing(true)
    setError(null)
    setMessage(null)
    try {
      const command = preparePaperDeposit(pendingRef.current, amount, token)
      pendingRef.current = command
      setPending(command)
      sessionStorage.setItem(storageKey, JSON.stringify(command.body))
      const acc = await depositPaperFunds(command.body, token)
      if (!active.current) return
      pendingRef.current = null
      setPending(null)
      sessionStorage.removeItem(storageKey)
      setAccount(acc)
      setMessage(`已充值 ¥${command.body.amount} 模拟资金`)
      const flow = await getPaperLedgers(10, 0, token)
      if (!active.current) return
      setLedgers(flow.items)
    } catch (err) {
      if (!active.current) return
      if (isDefinitivePaperDepositError(err)) {
        pendingRef.current = null
        setPending(null)
        sessionStorage.removeItem(storageKey)
      }
      setError(err instanceof Error ? err.message : "充值结果待确认，请重试原请求")
    } finally {
      busy.current = false
      if (active.current) setDepositing(false)
    }
  }

  if (loading && !account) {
    return (
      <div className="flex items-center gap-2 text-sm text-[var(--text-muted)] py-12">
        <Loader2 className="w-4 h-4 animate-spin" />
        加载虚拟账户…
      </div>
    )
  }

  return (
    <div className="max-w-2xl space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-[var(--text-primary)] flex items-center gap-2">
          <Wallet className="w-5 h-5 text-[var(--primary)]" />
          虚拟练习资金
        </h2>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void load()}
          disabled={loading || depositing}
          className="gap-1"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          刷新
        </Button>
      </div>

      <p className="text-xs text-[var(--text-muted)] leading-relaxed">
        自定义人民币模拟资金，用于期货开平仓和保证金占用，不设日或月领取额度。
        仅在本系统本地模拟，不接真实资金，不可提现。
      </p>

      {error && (
        <div className="text-sm text-[var(--accent-danger)] bg-[var(--accent-danger)]/10 rounded-md px-3 py-2">
          {error}
        </div>
      )}
      {storageError && <p className="text-sm text-[var(--accent-danger)]">{storageError}。请先核对待确认充值记录后再操作。</p>}
      {message && (
        <div className="text-sm text-[var(--accent-up)] bg-[var(--accent-up)]/10 rounded-md px-3 py-2">
          {message}
        </div>
      )}

      {/* 余额卡片 */}
      <Card>
        <CardContent className="p-4 space-y-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-xs text-[var(--text-muted)]">{valuation.equityLabel}</p>
              <p className="text-2xl font-num font-semibold text-[var(--text-primary)] mt-1">
                {valuation.equity === null ? "—" : `¥${formatMoney(valuation.equity)}`}
              </p>
            </div>
            <Badge variant="outline">本地模拟</Badge>
          </div>
          <Separator />
          <div className="grid grid-cols-2 gap-3 text-sm">
            <Metric label="可用保证金" value={account?.available_margin ?? 0} />
            <Metric label="冻结保证金" value={account?.frozen_margin ?? 0} />
            <Metric label="累计模拟入金" value={account?.total_deposit ?? 0} />
            <Metric label="已实现盈亏" value={account?.realized_pnl ?? 0} signed />
          </div>
          <div className="space-y-3 pt-1">
            <p className="text-xs text-[var(--text-muted)]">
              参考风险率{" "}
              {valuation.risk === null ? "—" : `${valuation.risk.toFixed(2)}%`}
            </p>
            <p className="text-xs text-[var(--text-muted)]">{valuation.note}</p>
            <label htmlFor="paper-deposit-amount" className="text-xs text-[var(--text-secondary)]">充值金额（人民币）</label>
            <Input id="paper-deposit-amount" inputMode="decimal" value={amount} disabled={depositing || pending !== null} onChange={(event) => setAmount(event.target.value)} autoComplete="off" />
            {pending && <p className="text-xs text-[var(--text-muted)]">上一笔充值待确认；重试将复用原金额和请求编号，避免重复入金。</p>}
            <Button
              size="sm"
              className="gap-1"
              disabled={!account?.custom_deposit_enabled || depositing || loading || storageError !== null}
              onClick={() => void handleDeposit()}
            >
              {depositing ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Gift className="w-3.5 h-3.5" />
              )}
              {depositing ? "提交中…" : pending ? "重试待确认充值" : "充值模拟资金"}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* 近期流水 */}
      <Card>
        <CardContent className="p-4 space-y-3">
          <p className="text-sm font-medium text-[var(--text-primary)]">近期资金流水</p>
          <Separator />
          {ledgers.length === 0 ? (
            <p className="text-xs text-[var(--text-muted)] py-4 text-center">
              暂无流水，充值模拟资金后会出现记录
            </p>
          ) : (
            <div className="space-y-2">
              {ledgers.map((row) => (
                <div
                  key={row.id}
                  className="flex items-center gap-3 text-xs py-1.5 border-b border-[var(--border)]/40 last:border-0"
                >
                  <span className="font-num text-[var(--text-muted)] w-[140px] shrink-0">
                    {formatShanghaiTime(row.time)}
                  </span>
                  <span className="text-[var(--text-secondary)] w-[64px] shrink-0">
                    {flowTypeLabel(row.type)}
                  </span>
                  <span
                    className={`font-num w-[100px] shrink-0 ${
                      row.amount >= 0
                        ? "text-[var(--accent-up)]"
                        : "text-[var(--accent-danger)]"
                    }`}
                  >
                    {row.amount >= 0 ? "+" : ""}
                    {formatMoney(row.amount)}
                  </span>
                  <span className="text-[var(--text-muted)] truncate flex-1">
                    {row.description}
                  </span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function Metric(props: {
  label: string
  value: number
  signed?: boolean
}): React.JSX.Element {
  const color =
    props.signed && props.value !== 0
      ? props.value > 0
        ? "text-[var(--accent-up)]"
        : "text-[var(--accent-danger)]"
      : "text-[var(--text-primary)]"
  return (
    <div className="rounded-md bg-[var(--bg-tertiary)]/50 px-3 py-2">
      <p className="text-[10px] text-[var(--text-muted)]">{props.label}</p>
      <p className={`text-sm font-num mt-0.5 ${color}`}>
        {props.signed && props.value > 0 ? "+" : ""}¥{formatMoney(props.value)}
      </p>
    </div>
  )
}
