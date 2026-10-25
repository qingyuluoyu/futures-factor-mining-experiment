"use client"

import { useEffect } from "react"
import Link from "next/link"
import { cn, formatShanghaiTime } from "@/lib/utils"
import { usePaperTradingStore } from "@/stores/paper-trading"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table"
import { Loader2 } from "lucide-react"
import type { PaperLedgerItem } from "@/lib/paper-api"
import { useAuthStore } from "@/stores/auth"
import { StockReplayWorkspace } from "@/components/trading/stock-replay-workspace"
import { paperValuationView, signedPaperMoney } from "@/lib/paper-valuation"

export default function AssetsPage(): React.JSX.Element {
  const market = useAuthStore((state) => state.user?.market_type)
  return market === "stock" ? <StockReplayWorkspace initialTab="assets" /> : <FuturesAssetsPage />
}

function formatMoney(v: number | null): string {
  if (v === null) return "—"
  return v.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

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

/** 资产中心 —— 模拟账户真实数据 */
function FuturesAssetsPage(): React.JSX.Element {
  const account = usePaperTradingStore((s) => s.account)
  const ledgers = usePaperTradingStore((s) => s.ledgers)
  const loading = usePaperTradingStore((s) => s.loading)
  const refresh = usePaperTradingStore((s) => s.refresh)

  useEffect(() => {
    void refresh()
  }, [refresh])

  const valuation = paperValuationView(account)
  const cards = [
    {
      label: valuation.equityLabel,
      value: formatMoney(valuation.equity),
    },
    {
      label: "可用保证金",
      value: formatMoney(account?.available_margin ?? 0),
    },
    {
      label: "冻结保证金",
      value: formatMoney(account?.frozen_margin ?? 0),
    },
    {
      label: "已实现盈亏",
      value: account?.realized_pnl ?? 0,
      isPnl: true,
    },
    {
      label: "浮动盈亏",
      value: signedPaperMoney(valuation.floating),
    },
    {
      label: "历史领取",
      value: formatMoney(account?.total_claimed ?? 0),
    },
    {
      label: "累计入金",
      value: formatMoney(account?.total_deposit ?? 0),
    },
    {
      label: "参考风险率",
      value: valuation.risk === null ? "—" : `${valuation.risk.toFixed(2)}%`,
    },
  ]

  return (
    <div className="flex flex-col h-full overflow-auto p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-[var(--text-primary)]">
            资产中心
          </h1>
          <p className="text-xs text-[var(--text-muted)] mt-0.5">
            模拟账户 · 自定义模拟资金 · 不接真实资金
          </p>
        </div>
        <div className="flex gap-2">
          <Button asChild variant="outline" size="sm">
            <Link href="/profile">充值模拟资金</Link>
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="gap-1"
            disabled={loading}
            onClick={() => void refresh()}
          >
            {loading && <Loader2 className="w-3 h-3 animate-spin" />}
            刷新
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-3">
        {cards.map((c) => (
          <Card key={c.label}>
            <CardContent className="p-3">
              <p className="text-xs text-[var(--text-muted)]">{c.label}</p>
              <p
                className={cn(
                  "text-base font-semibold font-num mt-1",
                  c.isPnl
                    ? (c.value as number) >= 0
                      ? "text-up"
                      : "text-down"
                    : "text-[var(--text-primary)]"
                )}
              >
                {c.isPnl
                  ? `${(c.value as number) >= 0 ? "+" : ""}${formatMoney(c.value as number)}`
                  : c.value}
              </p>
            </CardContent>
          </Card>
        ))}
      </div>
      <p className="text-xs text-[var(--text-muted)]">{valuation.note}</p>

      <Tabs defaultValue="flows">
        <TabsList>
          <TabsTrigger value="flows">资金流水</TabsTrigger>
          <TabsTrigger value="claim">模拟资金说明</TabsTrigger>
        </TabsList>

        <TabsContent value="flows">
          <Card>
            <CardContent className="p-0">
              <FundFlowTable flows={ledgers} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="claim">
          <Card>
            <CardContent className="p-4 space-y-2 text-sm text-[var(--text-secondary)]">
              <p>
                在「个人中心 → 虚拟资金」输入所需金额，即可充值人民币模拟资金。
              </p>
              <p>
                不设日或月领取额度。充值会增加可用资金，并保留当前持仓、冻结保证金和盈亏。
              </p>
              <p className="text-xs text-[var(--text-muted)]">
                资金仅用于本系统模拟交易，不可提现。手续费与保证金按期货品种规则计算，可在交易设置中调整。
              </p>
              <Button asChild size="sm" className="mt-2">
                <Link href="/profile">前往充值</Link>
              </Button>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}

function FundFlowTable(props: { flows: PaperLedgerItem[] }): React.JSX.Element {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>时间</TableHead>
          <TableHead>类型</TableHead>
          <TableHead>金额</TableHead>
          <TableHead>余额</TableHead>
          <TableHead>说明</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {props.flows.length === 0 ? (
          <TableRow>
            <TableCell
              colSpan={5}
              className="text-center text-[var(--text-muted)] py-8"
            >
              暂无流水，领取练习金或下单后会出现记录
            </TableCell>
          </TableRow>
        ) : (
          props.flows.map((f) => (
            <TableRow key={f.id}>
              <TableCell className="font-num text-xs text-[var(--text-muted)]">
                {formatShanghaiTime(f.time)}
              </TableCell>
              <TableCell>
                <Badge variant="outline">{flowTypeLabel(f.type)}</Badge>
              </TableCell>
              <TableCell
                className={cn(
                  "font-num text-sm",
                  f.amount >= 0 ? "text-up" : "text-down"
                )}
              >
                {f.amount >= 0 ? "+" : ""}
                {formatMoney(f.amount)}
              </TableCell>
              <TableCell className="font-num text-sm">
                {formatMoney(f.balance)}
              </TableCell>
              <TableCell className="text-xs text-[var(--text-secondary)]">
                {f.description}
              </TableCell>
            </TableRow>
          ))
        )}
      </TableBody>
    </Table>
  )
}
