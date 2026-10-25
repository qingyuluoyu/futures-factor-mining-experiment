/** 界面主题（浅色/深色）—— 本地持久化，默认浅色（白底） */

export type Theme = "light" | "dark"

export const THEME_STORAGE_KEY = "qihuo_theme"

export interface ThemeOption {
  value: Theme
  label: string
  description: string
}

/** 可选主题：浅色（默认）→ 深色 */
export const THEME_OPTIONS: ThemeOption[] = [
  {
    value: "light",
    label: "浅色",
    description: "白底界面，日间盯盘更清晰",
  },
  {
    value: "dark",
    label: "深色",
    description: "暗色暖灰界面，夜间盯盘更护眼",
  },
]

/** 规范化主题值，非法值回落浅色（默认） */
export function normalizeTheme(value: string | null | undefined): Theme {
  return value === "dark" ? "dark" : "light"
}

/** 从 localStorage 读取（仅浏览器；默认浅色） */
export function readStoredTheme(): Theme {
  if (typeof window === "undefined") {
    return "light"
  }
  try {
    return normalizeTheme(window.localStorage.getItem(THEME_STORAGE_KEY))
  } catch {
    return "light"
  }
}

/**
 * 应用主题到 <html>：dark class（Tailwind 变体 + .dark 变量块）
 * 与 data-theme 属性（供非样式逻辑读取）同步，并写入 localStorage。
 */
export function applyTheme(theme: Theme): void {
  const next = normalizeTheme(theme)
  if (typeof document !== "undefined") {
    const root = document.documentElement
    root.classList.toggle("dark", next === "dark")
    root.setAttribute("data-theme", next)
  }
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next)
    } catch {
      // 隐私模式等写失败时仍保持本次 DOM 生效
    }
  }
}

/** 读取当前生效主题（SSR 安全，默认浅色） */
export function getCurrentTheme(): Theme {
  if (typeof document === "undefined") {
    return "light"
  }
  return document.documentElement.classList.contains("dark") ? "dark" : "light"
}
