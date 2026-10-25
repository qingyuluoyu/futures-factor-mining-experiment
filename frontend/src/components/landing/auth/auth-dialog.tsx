"use client"

/**
 * 首页登录/注册弹窗 —— Tab 切换 + 模态面板
 */

import { useEffect, useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog"
import { AuthLoginForm } from "./auth-login-form"
import { AuthRegisterForm } from "./auth-register-form"
import { BrandLogo } from "@/components/common/brand-logo"
import { BRAND_NAME, BRAND_TAGLINE } from "@/lib/brand"

export type AuthMode = "login" | "register"

interface AuthDialogProps {
  open: boolean
  initialMode: AuthMode
  onClose: () => void
}

/** 登录/注册弹窗 */
export function AuthDialog({
  open,
  initialMode,
  onClose,
}: AuthDialogProps): React.JSX.Element {
  const [mode, setMode] = useState<AuthMode>(initialMode)

  // 弹窗打开或外部 initialMode 变化时同步
  useEffect(() => {
    if (open) setMode(initialMode)
  }, [open, initialMode])

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-md max-h-[88vh] overflow-y-auto border-[var(--border)] bg-[var(--bg-secondary)]/95 backdrop-blur-xl">
        <div className="flex flex-col items-center mb-4">
          <BrandLogo size={44} decorative className="mb-3" />
          <DialogTitle className="text-base font-semibold text-[var(--text-primary)]">
            {mode === "login" ? `登录${BRAND_NAME}` : `注册${BRAND_NAME}`}
          </DialogTitle>
          <DialogDescription className="text-[11px] text-[var(--text-muted)] mt-1">
            {BRAND_TAGLINE}
          </DialogDescription>
        </div>

        {/* Tab 切换 */}
        <div className="grid grid-cols-2 gap-1 p-1 rounded-lg bg-[var(--bg-tertiary)] border border-[var(--border)] mb-4">
          <button
            type="button"
            onClick={() => setMode("login")}
            className={`h-8 rounded-md text-xs font-medium transition-colors ${
              mode === "login"
                ? "bg-red-500/25 text-red-100 border border-red-500/40"
                : "text-[var(--text-muted)] hover:text-[var(--text-primary)] border border-transparent"
            }`}
          >
            登录
          </button>
          <button
            type="button"
            onClick={() => setMode("register")}
            className={`h-8 rounded-md text-xs font-medium transition-colors ${
              mode === "register"
                ? "bg-red-500/25 text-red-100 border border-red-500/40"
                : "text-[var(--text-muted)] hover:text-[var(--text-primary)] border border-transparent"
            }`}
          >
            注册
          </button>
        </div>

        {mode === "login" ? (
          <AuthLoginForm
            onSuccess={onClose}
            onSwitchRegister={() => setMode("register")}
          />
        ) : (
          <AuthRegisterForm
            onSuccess={onClose}
            onSwitchLogin={() => setMode("login")}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}
