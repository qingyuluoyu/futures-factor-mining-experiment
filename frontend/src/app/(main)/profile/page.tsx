"use client"

import { useState } from "react"
import { cn } from "@/lib/utils"
import { User, Shield, Sliders, Bell, Wallet, Type, Volume2 } from "lucide-react"
import { PersonalInfoPanel } from "@/components/profile/personal-info-panel"
import { VirtualFundsPanel } from "@/components/portfolio/virtual-funds-panel"
import { TradeSettingsPanel } from "@/components/portfolio/trade-settings-panel"
import { SecurityPanel } from "@/components/profile/security-panel"
import { AlertSettingsPanel } from "@/components/profile/alert-settings-panel"
import { DisplaySettingsPanel } from "@/components/profile/display-settings-panel"
import { VoiceBroadcastPanel } from "@/components/profile/voice-broadcast-panel"
import { useAuthStore } from "@/stores/auth"
import { supportsStockProfileTab } from "@/lib/market-routes"

/** 个人中心页面 —— 左右分栏 + Tab 切换 */
export default function ProfilePage(): React.JSX.Element {
  const [activeTab, setActiveTab] = useState("info")
  const stockMarket = useAuthStore((state) => state.user?.market_type === "stock")
  const identityKey = useAuthStore((state) => `${state.user?.id ?? "anonymous"}:${state.sessionRevision}`)
  const visibleTab = stockMarket && !supportsStockProfileTab(activeTab) ? "info" : activeTab

  return (
    <div className="flex h-full overflow-auto">
      {/* 左侧 Tab 导航 */}
      <div className="w-[180px] shrink-0 border-r border-[var(--border)] bg-[var(--bg-secondary)] p-3 space-y-1">
        <p className="text-xs text-[var(--text-muted)] mb-3 px-2">个人中心</p>
        {[
          { value: "info", icon: User, label: "个人信息" },
          { value: "display", icon: Type, label: "显示设置" },
          { value: "funds", icon: Wallet, label: "虚拟资金" },
          { value: "security", icon: Shield, label: "安全设置" },
          { value: "trading", icon: Sliders, label: "交易设置" },
          { value: "alerts", icon: Bell, label: "提醒设置" },
          { value: "voice", icon: Volume2, label: "语音播报" },
        ].filter((tab) => !stockMarket || supportsStockProfileTab(tab.value)).map((tab) => (
          <button
            key={tab.value}
            onClick={() => setActiveTab(tab.value)}
            className={cn(
              "w-full flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors cursor-pointer",
              visibleTab === tab.value
                ? "bg-[var(--primary)]/20 text-[var(--primary)]"
                : "text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]"
            )}
          >
            <tab.icon className="w-4 h-4" />
            {tab.label}
          </button>
        ))}
      </div>

      {/* 右侧内容 */}
      <div className="flex-1 p-6 overflow-auto">
        {visibleTab === "info" && <PersonalInfoPanel key={identityKey} />}
        {visibleTab === "display" && <DisplaySettingsPanel />}
        {!stockMarket && visibleTab === "funds" && <VirtualFundsPanel />}
        {visibleTab === "security" && <SecurityPanel />}
        {!stockMarket && visibleTab === "trading" && <TradeSettingsPanel />}
        {!stockMarket && visibleTab === "alerts" && <AlertSettingsPanel />}
        {!stockMarket && visibleTab === "voice" && <VoiceBroadcastPanel />}
      </div>
    </div>
  )
}

/** 开关行组件 */
function ToggleRow(props: { label: string; description: string; checked: boolean; onChange: (v: boolean) => void }): React.JSX.Element {
  return (
    <div className="flex items-center justify-between py-2">
      <div>
        <p className="text-sm text-[var(--text-secondary)]">{props.label}</p>
        <p className="text-xs text-[var(--text-muted)] mt-0.5">{props.description}</p>
      </div>
      <button
        onClick={() => props.onChange(!props.checked)}
        className={cn(
          "w-8 h-4 rounded-full transition-colors relative cursor-pointer",
          props.checked ? "bg-[var(--primary)]" : "bg-[var(--bg-tertiary)]"
        )}
      >
        <span className={cn(
          "absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform",
          props.checked ? "left-[18px]" : "left-0.5"
        )} />
      </button>
    </div>
  )
}
