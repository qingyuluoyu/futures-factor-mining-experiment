"use client"

import { useEffect, useState } from "react"
import { TopNavbar } from "@/components/layout/top-navbar"
import { Sidebar } from "@/components/layout/sidebar"
import { FinancialSessionSync } from "@/components/layout/financial-session-sync"
import { ContractSearchDialog } from "@/components/market/contract-search-dialog"
import { FloatingAssistant } from "@/components/ai/floating-assistant"
import { MessagePopup } from "@/components/notifications/message-popup"
import { BigOrderToastPopup } from "@/components/notifications/big-order-toast"
import { startVoiceBroadcastEngine } from "@/lib/voice-broadcast-engine"
import { TaskAlertToastPopup } from "@/components/notifications/task-alert-toast"
import { GlobalDialog } from "@/components/global-dialog"
import { useKeyboardShortcuts } from "@/hooks/keyboard"
import { useTabSync } from "@/hooks/sync"
import { useAuthGuard } from "@/hooks/auth"
import { useNotificationPreferencesStore } from "@/stores/notification-preferences"
import { setSpeechSettings } from "@/lib/speech"
import { ensureContractNames } from "@/lib/contract-names"
import { useBigOrderStore } from "@/stores/big-order"
import { useTaskAlertStore } from "@/stores/task-alert"
import { useAuthStore } from "@/stores/auth"
import { useMarketStore } from "@/stores/market"
import { useAppStore, migrateStaleContract, readActiveContract } from "@/stores/app"
import { broadcastContractChange } from "@/hooks/sync"
import { useStockMarketStore } from "@/stores/stock-market"
import { StockFeatureBoundary } from "@/components/layout/stock-feature-boundary"

/** 用户端布局 Shell（顶部导航 + 侧边栏 + 主内容区） */
export default function MainLayout({
  children,
}: {
  children: React.ReactNode
}): React.JSX.Element {
  const [mounted, setMounted] = useState(false)
  const accessToken = useAuthStore((s) => s.accessToken)
  const sessionLoaded = useAuthStore((s) => s.loaded)
  const sessionRevision = useAuthStore((s) => s.sessionRevision)
  const userId = useAuthStore((s) => s.user?.id)
  const tradingMode = useAuthStore((s) => s.user?.trading_mode ?? "live")
  const stockMarket = useAuthStore((s) => s.user?.market_type === "stock")

  useEffect(() => {
    setMounted(true)
  }, [])

  // 拉取通知设置 → 初始化提示音开关；同时拉未读数；加载大单预警/任务预警设置
  useEffect(() => {
    let cancelled = false
    if (!accessToken || !sessionLoaded || !userId) return
    void (async () => {
        await Promise.allSettled([
          useNotificationPreferencesStore.getState().initialize(),
          useBigOrderStore.getState().loadFromServer(),
          useTaskAlertStore.getState().loadFromServer(),
          ...(!stockMarket ? [ensureContractNames()] : []),
        ])
        if (!cancelled) {
          setSpeechSettings({
            voice_enabled: useBigOrderStore.getState().settings.voice_enabled,
          })
        }
    })()
    return () => {
      cancelled = true
    }
  }, [accessToken, stockMarket, sessionLoaded, userId, sessionRevision])

  // 大单设置变化时同步语音开关到 speech 模块
  const voiceEnabled = useBigOrderStore((s) => s.settings.voice_enabled)
  useEffect(() => {
    setSpeechSettings({ voice_enabled: voiceEnabled })
  }, [voiceEnabled])

  // 语音播报引擎：全局常驻（整分对齐调度 + 排队播报），设置见个人中心
  useEffect(() => {
    startVoiceBroadcastEngine()
  }, [])

  useKeyboardShortcuts()
  useTabSync()
  useAuthGuard()

  // 全局行情连接：登录拿到 token 后接入 /ws/market，让持仓、历史、订单等
  // 所有子页都能拿到实时现价（浮动盈亏由前端用 last_price 客户端计算）。
  // initWebSocket 幂等，token 缺失时 connect() 会跳过，登录后再次触发即建连。
  useEffect(() => {
    if (userId) useStockMarketStore.getState().restoreContext(userId, tradingMode)
  }, [userId, tradingMode])
  const initWebSocket = useMarketStore((s) => s.initWebSocket)
  useEffect(() => {
    if (accessToken) initWebSocket()
  }, [accessToken, initWebSocket])

  // 全局合约记忆恢复 + 过期迁移：持久化此前只写不读，刷新即落回出厂默认
  // rb2610（主力换月后即「过期合约」）。恢复记忆后与合约树比对：出厂
  // 默认/已下市 → 品种主力；用户主动选择的有效合约（次主力/远月）保留。
  useEffect(() => {
    if (stockMarket || !accessToken) return
    const saved = readActiveContract()
    if (saved) useAppStore.getState().setActiveContract(saved)
    void useMarketStore.getState().fetchCodeTree().then(() => {
      const tree = useMarketStore.getState().codeTree
      const current = useAppStore.getState().activeContract
      const migrated = migrateStaleContract(current, tree)
      if (migrated !== current) {
        useAppStore.getState().setActiveContract(migrated)
        broadcastContractChange(migrated)
      }
    })
  }, [stockMarket, accessToken])

  if (!mounted || !sessionLoaded) {
    return (
      <div className="flex flex-col h-screen overflow-hidden bg-[var(--bg-primary)]">
        <div className="h-[56px] border-b border-[var(--border)] bg-[var(--bg-secondary)] shrink-0" />
        <div className="flex flex-1 overflow-hidden">
          <aside className="w-[192px] border-r border-[var(--border)] bg-[var(--bg-secondary)]" />
          <main role="status" className="flex-1 overflow-auto p-8 text-sm text-[var(--text-muted)]">正在校验登录会话…</main>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-[var(--bg-primary)]">
      <TopNavbar />
      <FinancialSessionSync />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-auto">
          <StockFeatureBoundary>{children}</StockFeatureBoundary>
        </main>
      </div>
      <ContractSearchDialog />
      {/* 全局 AI 交易助手：浮空按钮 / 可缩放浮窗 */}
      {!stockMarket && <FloatingAssistant />}
      {/* 消息弹窗：右下角，WS 推送新消息时滑入 */}
      <MessagePopup />
      {/* 大单预警弹窗：右下角堆叠，命中阈值时显示 10 秒（不入库） */}
      <BigOrderToastPopup />
      {/* 任务预警弹窗：任务下单/平仓成交时滑入（不入库） */}
      <TaskAlertToastPopup />
      {/* 全局美化弹窗：showAlert / showConfirm 的渲染器 */}
      <GlobalDialog />
    </div>
  )
}
