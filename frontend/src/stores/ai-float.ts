/**
 * 全局 AI 浮窗 UI 状态 —— 开关、尺寸、按钮/窗口各自位置
 * 持久化到 localStorage，全站共享
 */

import { create } from "zustand"

const STORAGE_KEY = "ai:float-window"
const MIN_W = 320
const MAX_W = 1120
const MIN_H = 360
const MAX_H = 900
const DEFAULT_W = 640
const DEFAULT_H = 640
const DEFAULT_RIGHT = 24
const DEFAULT_BOTTOM = 24

interface PersistedState {
  open: boolean
  width: number
  height: number
  /** 展开窗口：距视口右边 */
  winRight: number
  /** 展开窗口：距视口底边 */
  winBottom: number
  /** 浮空按钮：距视口右边 */
  fabRight: number
  /** 浮空按钮：距视口底边 */
  fabBottom: number
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

function defaults(): PersistedState {
  return {
    open: false,
    width: DEFAULT_W,
    height: DEFAULT_H,
    winRight: DEFAULT_RIGHT,
    winBottom: DEFAULT_BOTTOM,
    fabRight: DEFAULT_RIGHT,
    fabBottom: DEFAULT_BOTTOM,
  }
}

function loadPersisted(): PersistedState {
  if (typeof window === "undefined") return defaults()
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return defaults()
    const data = JSON.parse(raw) as Partial<PersistedState> & {
      right?: number
      bottom?: number
    }
    const maxW = window.innerWidth - 16
    const maxH = window.innerHeight - 64
    // 兼容旧字段 right/bottom
    const legacyR = Number(data.right)
    const legacyB = Number(data.bottom)
    const winR = Number(data.winRight ?? legacyR)
    const winB = Number(data.winBottom ?? legacyB)
    const fabR = Number(data.fabRight ?? legacyR)
    const fabB = Number(data.fabBottom ?? legacyB)
    const width = clamp(
      Number(data.width) || DEFAULT_W,
      Math.min(MIN_W, maxW),
      Math.min(MAX_W, maxW),
    )
    const height = clamp(
      Number(data.height) || DEFAULT_H,
      Math.min(MIN_H, maxH),
      Math.min(MAX_H, maxH),
    )
    // 位置按当前视口钳回可见区域：拖动时虽已收拢，但换小窗口/浏览器缩放/
    // 换显示器后，旧 right/bottom 可能把球或窗口整体放到屏幕外（球"消失"）
    const win = clampOffset(
      Number.isFinite(winR) ? winR : DEFAULT_RIGHT,
      Number.isFinite(winB) ? winB : DEFAULT_BOTTOM,
      width,
      height,
    )
    const fab = clampOffset(
      Number.isFinite(fabR) ? fabR : DEFAULT_RIGHT,
      Number.isFinite(fabB) ? fabB : DEFAULT_BOTTOM,
      120,
      48,
    )
    return {
      open: Boolean(data.open),
      width,
      height,
      winRight: win.right,
      winBottom: win.bottom,
      fabRight: fab.right,
      fabBottom: fab.bottom,
    }
  } catch {
    return defaults()
  }
}

function persist(state: PersistedState): void {
  if (typeof window === "undefined") return
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch {
    // ignore
  }
}

/** 将窗口与按钮的位置约束在当前视口内 */
export function clampOffset(
  right: number,
  bottom: number,
  elemWidth: number,
  elemHeight: number
): { right: number; bottom: number } {
  if (typeof window === "undefined") {
    return { right, bottom }
  }
  const vw = window.innerWidth
  const vh = window.innerHeight
  return {
    right: clamp(right, 0, Math.max(0, vw - elemWidth)),
    bottom: clamp(bottom, 0, Math.max(0, vh - elemHeight)),
  }
}

interface AiFloatState {
  open: boolean
  width: number
  height: number
  winRight: number
  winBottom: number
  fabRight: number
  fabBottom: number
  hydrated: boolean
  restoreWidth: number | null
  toggleWide: () => void
  hydrate: () => void
  openPanel: () => void
  closePanel: () => void
  setSize: (width: number, height: number) => void
  setWinOffset: (right: number, bottom: number) => void
  setFabOffset: (right: number, bottom: number) => void
}

function snapshot(s: AiFloatState): PersistedState {
  return {
    open: s.open,
    width: s.width,
    height: s.height,
    winRight: s.winRight,
    winBottom: s.winBottom,
    fabRight: s.fabRight,
    fabBottom: s.fabBottom,
  }
}

export const useAiFloatStore = create<AiFloatState>((set, get) => ({
  open: false,
  width: DEFAULT_W,
  height: DEFAULT_H,
  winRight: DEFAULT_RIGHT,
  winBottom: DEFAULT_BOTTOM,
  fabRight: DEFAULT_RIGHT,
  fabBottom: DEFAULT_BOTTOM,
  hydrated: false,
  restoreWidth: null,

  hydrate: () => {
    if (get().hydrated) return
    const saved = loadPersisted()
    set({ ...saved, hydrated: true })
    get().setSize(saved.width, saved.height)
    get().setWinOffset(saved.winRight, saved.winBottom)
  },

  openPanel: () => {
    set({ open: true })
    persist({ ...snapshot(get()), open: true })
  },

  closePanel: () => {
    set({ open: false })
    persist({ ...snapshot(get()), open: false })
  },

  toggleWide: () => {
    const state = get()
    const savedWidth = state.restoreWidth
    get().setSize(savedWidth ?? MAX_W, state.height)
    set({ restoreWidth: savedWidth === null ? state.width : null })
  },

  setSize: (width: number, height: number) => {
    const maxW =
      typeof window !== "undefined" ? window.innerWidth - 16 : MAX_W
    const maxH =
      typeof window !== "undefined" ? window.innerHeight - 64 : MAX_H
    const w = clamp(width, Math.min(MIN_W, maxW), Math.min(MAX_W, maxW))
    const h = clamp(height, Math.min(MIN_H, maxH), Math.min(MAX_H, maxH))
    const offset = clampOffset(get().winRight, get().winBottom, w, h)
    set({ width: w, height: h, winRight: offset.right, winBottom: offset.bottom })
    persist({ ...snapshot(get()), width: w, height: h })
  },

  setWinOffset: (right: number, bottom: number) => {
    const { width, height } = get()
    const next = clampOffset(right, bottom, width, height)
    set({ winRight: next.right, winBottom: next.bottom })
    persist({
      ...snapshot(get()),
      winRight: next.right,
      winBottom: next.bottom,
    })
  },

  setFabOffset: (right: number, bottom: number) => {
    // 浮空按钮约 120x48
    const next = clampOffset(right, bottom, 120, 48)
    set({ fabRight: next.right, fabBottom: next.bottom })
    persist({
      ...snapshot(get()),
      fabRight: next.right,
      fabBottom: next.bottom,
    })
  },
}))
