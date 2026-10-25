"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"
import * as DropdownMenu from "@radix-ui/react-dropdown-menu"
import { Bell, Search, ChevronDown, Circle, LogOut, User, Shield, Sun, Moon } from "lucide-react"
import { useAppStore } from "@/stores/app"
import { useAuthStore } from "@/stores/auth"
import { useNotificationsStore } from "@/stores/notifications"
import { useNotificationPreferencesStore } from "@/stores/notification-preferences"
import { useDisplayStore } from "@/stores/display"
import { useSessionStatus } from "@/hooks/use-session-status"
import { cn } from "@/lib/utils"
import { BrandLogo } from "@/components/common/brand-logo"
import { BRAND_NAME } from "@/lib/brand"

/** 市场状态配置 */
const MARKET_STATUS_MAP = {
  trading: { color: "bg-[var(--accent-down)]", label: "交易中" },
  closed: { color: "bg-[var(--accent-warn)]", label: "休市" },
  offline: { color: "bg-[var(--accent-danger)] animate-pulse", label: "断线" },
} as const

/** 顶部导航栏（56px 固定高度） */
export function TopNavbar(): React.JSX.Element {
  const router = useRouter()
  const { marketStatus, setMarketStatus, setSearchOpen, activeContract } = useAppStore()
  const { user, logout } = useAuthStore()
  const stockMarket = user?.market_type === "stock"
  const switchMarket = useAuthStore((s) => s.switchMarket)
  const marketSwitchError = useAuthStore((s) => s.marketSwitchError)
  const theme = useDisplayStore((s) => s.theme)
  const setTheme = useDisplayStore((s) => s.setTheme)
  const { status: session, isOpen, hasVirtualQuote } = useSessionStatus(activeContract)
  const isVirtual =
    user?.trading_mode === "virtual" || session?.trading_mode === "virtual"

  // 通知未读数：订阅 store 单一数据源（顶栏红点 / 消息页 / 弹窗共用）。
  // WS 推送、消息页标记已读均实时写入 store，顶栏自动更新。
  // 仅保留低频兜底轮询，防止 WS 断线时数字僵死；写入 store 而非本地 state。
  const unread = useNotificationsStore((s) => s.unread)
  const preferences = useNotificationPreferencesStore()
  const fetchUnread = useNotificationsStore((s) => s.fetchUnread)
  useEffect(() => {
    if (!user) return
    void fetchUnread()
    const t = setInterval(() => void fetchUnread(), 60_000)
    return () => clearInterval(t)
  }, [user, fetchUnread])

  useEffect(() => {
    // 虚拟盘 7×24：顶栏状态灯恒为交易中，不跟国内周末休市
    if (isVirtual) {
      setMarketStatus("trading")
      return
    }
    if (session) {
      setMarketStatus(isOpen ? "trading" : "closed")
    }
  }, [session, isOpen, isVirtual, setMarketStatus])

  const status = MARKET_STATUS_MAP[marketStatus]
  const statusLabel = stockMarket ? "A股 · 历史日线 · 非实时" : isVirtual
    ? hasVirtualQuote === false
      ? "虚拟盘 · 暂无行情"
      : "虚拟盘 · 7×24"
    : session
      ? isOpen
        ? `交易中 · ${session.product_code}`
        : `休市 · ${session.product_code}`
      : status.label

  // user 未恢复前不显示「用户」，避免被误认为角色变成普通用户
  const displayName: string = user?.username ?? "加载中…"
  const displayInitial: string = user?.username
    ? user.username.charAt(0).toUpperCase()
    : "?"
  const modeLabel =
    user?.trading_mode === "virtual" ? "7×24模拟盘" : "市场行情模拟盘"

  /** 退出登录 */
  const handleLogout = (): void => {
    logout()
    router.replace("/login")
  }

  return (
    <header className="h-[56px] flex items-center justify-between px-4 border-b border-[var(--border)] bg-[var(--bg-secondary)] shrink-0 z-30">
      {/* 左侧：Logo + 状态灯 */}
      <div className="flex items-center gap-2 sm:gap-4">
        <div className="flex shrink-0 items-center gap-2">
          <BrandLogo decorative />
          <span className="text-[var(--text-primary)] font-semibold text-base whitespace-nowrap">
            {BRAND_NAME}
          </span>
        </div>
        <div
          className="hidden lg:flex items-center gap-1.5 text-xs text-[var(--text-muted)] whitespace-nowrap"
          title={session?.message ?? statusLabel}
        >
          <Circle className={cn("w-2 h-2 fill-current", status.color)} />
          <span>{statusLabel}</span>
        </div>
        {user && (
          <span
            className={cn(
              "hidden xl:inline-flex text-[10px] px-2 py-0.5 rounded border whitespace-nowrap",
              user.trading_mode === "virtual"
                ? "border-[var(--accent-warn)]/40 text-[var(--accent-warn)]"
                : "border-[var(--primary)]/40 text-[var(--primary)]",
            )}
            title="登录时选定，会话内不可切换"
          >
            {modeLabel}
          </span>
        )}

        {/* 市场切换：期货 / A股（双市场，持久化到用户） */}
        {user && (
          <div className="flex shrink-0 items-center rounded-md border border-[var(--border)] bg-[var(--bg-tertiary)] p-0.5 whitespace-nowrap">
            {(
              [
                { value: "futures" as const, label: "期货" },
                { value: "stock" as const, label: "A股" },
              ]
            ).map((item) => {
              const active = (user.market_type ?? "futures") === item.value
              return (
                <button
                  key={item.value}
                  onClick={() => {
                    const revision = useAuthStore.getState().sessionRevision
                    if (!active) void switchMarket(item.value).then(() => {
                      const current = useAuthStore.getState()
                      if (current.sessionRevision === revision && !current.marketSwitchError && current.user?.market_type === item.value) router.replace("/market")
                    })
                  }}
                  title={item.value === "futures" ? "期货市场" : "A股市场"}
                  className={cn(
                    "px-2 py-0.5 text-[11px] rounded transition-colors cursor-pointer",
                    active
                      ? "bg-[var(--primary)] text-white"
                      : "text-[var(--text-muted)] hover:text-[var(--text-primary)]",
                  )}
                >
                  {item.label}
                </button>
              )
            })}
            {marketSwitchError && <span role="alert" className="max-w-48 px-2 text-[11px] text-[var(--accent-danger)]">{marketSwitchError}</span>}
          </div>
        )}
      </div>

      {/* 右侧：搜索、通知、用户菜单 */}
      <div className="flex shrink-0 items-center gap-1 sm:gap-3">
        {/* 合约搜索按钮 */}
        <button
          onClick={() => setSearchOpen(true)}
          className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-[var(--bg-tertiary)] text-[var(--text-muted)] text-sm hover:bg-[var(--border)] transition-colors cursor-pointer"
        >
          <Search className="w-4 h-4" />
          <span className="hidden sm:inline">{stockMarket ? "搜索股票" : "搜索合约"}</span>
          <kbd className="hidden md:inline text-xs px-1.5 py-0.5 rounded bg-[var(--bg-primary)] border border-[var(--border)]">
            Ctrl+K
          </kbd>
        </button>

        {/* 主题切换：浅色（默认）/ 深色 */}
        <button
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          title={theme === "dark" ? "切换到浅色" : "切换到深色"}
          className="p-2 rounded-md hover:bg-[var(--bg-tertiary)] transition-colors cursor-pointer"
        >
          {theme === "dark" ? (
            <Sun className="w-4 h-4 text-[var(--text-secondary)]" />
          ) : (
            <Moon className="w-4 h-4 text-[var(--text-secondary)]" />
          )}
        </button>

        {/* 通知图标 */}
        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild>
            <button title="通知与提醒设置" aria-label="通知与提醒设置" className="relative p-2 rounded-md hover:bg-[var(--bg-tertiary)] transition-colors cursor-pointer">
              <Bell className="w-4 h-4 text-[var(--text-secondary)]" />
              {unread > 0 && (
                <span className="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 px-1 rounded-full bg-[var(--accent-danger)] text-white text-[10px] font-num flex items-center justify-center">
                  {unread > 99 ? "99+" : unread}
                </span>
              )}
            </button>
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content align="end" sideOffset={8} className="z-50 w-72 rounded-md border border-[var(--border)] bg-[var(--bg-secondary)] p-2 shadow-lg">
              <DropdownMenu.CheckboxItem checked={preferences.popupEnabled} disabled={!preferences.loaded || !preferences.supported || preferences.saving} onSelect={(event) => event.preventDefault()} onCheckedChange={(value) => void preferences.setPopupEnabled(value)} className="flex items-center justify-between rounded px-2 py-2 text-sm text-[var(--text-primary)] outline-none cursor-pointer data-[disabled]:opacity-50 data-[highlighted]:bg-[var(--bg-tertiary)]">
                <span>弹出提醒</span><span>{preferences.saving ? "保存中…" : preferences.popupEnabled ? "已开启" : "已关闭"}</span>
              </DropdownMenu.CheckboxItem>
              <p className="px-2 pb-2 text-xs text-[var(--text-muted)]">统一控制消息、大单与任务弹窗，分类开关仍生效；声音单独设置。</p>
              {preferences.error && <p role="alert" className="px-2 pb-2 text-xs text-[var(--accent-danger)]">{preferences.error}</p>}
              {!preferences.loaded && <DropdownMenu.Item onSelect={() => void preferences.initialize()} className="px-2 py-2 text-sm text-[var(--primary)] cursor-pointer outline-none">重新加载提醒偏好</DropdownMenu.Item>}
              <DropdownMenu.Separator className="my-1 h-px bg-[var(--border)]" />
              <DropdownMenu.Item onSelect={() => router.push("/messages")} className="rounded px-2 py-2 text-sm text-[var(--text-primary)] cursor-pointer outline-none data-[highlighted]:bg-[var(--bg-tertiary)]">消息中心{unread > 0 ? `（${unread}）` : ""}</DropdownMenu.Item>
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>

        {/* 用户下拉菜单 */}
        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild>
            <button className="flex items-center gap-2 px-2 py-1 rounded-md hover:bg-[var(--bg-tertiary)] transition-colors cursor-pointer outline-none">
              <div className="w-7 h-7 rounded-full bg-[var(--primary)] flex items-center justify-center text-white text-xs font-medium">
                {displayInitial}
              </div>
              <span className="text-sm text-[var(--text-secondary)] hidden sm:inline">{displayName}</span>
              <ChevronDown className="w-3 h-3 text-[var(--text-muted)]" />
            </button>
          </DropdownMenu.Trigger>

          <DropdownMenu.Portal>
            <DropdownMenu.Content
              align="end"
              sideOffset={8}
              className="min-w-[160px] rounded-lg border border-[var(--border)] bg-[var(--bg-secondary)] p-1.5 shadow-xl z-50 animate-in fade-in-0 zoom-in-95"
            >
              {/* 用户信息 */}
              <DropdownMenu.Label className="px-3 py-2 text-xs text-[var(--text-muted)]">
                <User className="w-3 h-3 inline mr-1.5 -mt-0.5" />
                {displayName}
                {user?.role === "admin" ? " · 管理员" : ""}
              </DropdownMenu.Label>
              <DropdownMenu.Separator className="h-px bg-[var(--border)] my-1" />

              {user?.role === "admin" && (
                <DropdownMenu.Item
                  onSelect={() => router.push("/admin/dashboard")}
                  className="flex items-center gap-2 px-3 py-2 text-sm text-[var(--text-primary)] rounded-md cursor-pointer outline-none hover:bg-[var(--bg-tertiary)] transition-colors"
                >
                  <Shield className="w-4 h-4" />
                  管理后台
                </DropdownMenu.Item>
              )}

              {/* 退出登录 */}
              <DropdownMenu.Item
                onSelect={handleLogout}
                className="flex items-center gap-2 px-3 py-2 text-sm text-[var(--accent-danger)] rounded-md cursor-pointer outline-none hover:bg-[var(--bg-tertiary)] transition-colors"
              >
                <LogOut className="w-4 h-4" />
                退出登录
              </DropdownMenu.Item>
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
      </div>
    </header>
  )
}
