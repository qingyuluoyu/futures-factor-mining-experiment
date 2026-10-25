"use client"

import { useEffect, useRef } from "react"
import { isFuturesSymbol, useAppStore } from "@/stores/app"
import { useAuthStore } from "@/stores/auth"

/** 多标签页状态同步 Hook（BroadcastChannel API） */
export function useTabSync(): void {
  const channelRef = useRef<BroadcastChannel | null>(null)
  const { setActiveContract } = useAppStore()
  const userId = useAuthStore((state) => state.user?.id)
  const tradingMode = useAuthStore((state) => state.user?.trading_mode)

  useEffect(() => {
    if (!userId || typeof BroadcastChannel === "undefined") return
    const channel = new BroadcastChannel(`qihuo-sync:${userId}:${tradingMode}:futures`)
    channelRef.current = channel

    channel.onmessage = (event: MessageEvent) => {
      const data = event.data as { type: string; payload: unknown }

      switch (data.type) {
        case "active-contract": {
          if (typeof data.payload === "string" && isFuturesSymbol(data.payload)) setActiveContract(data.payload)
          break
        }
        default:
          break
      }
    }

    return () => {
      channel.close()
      channelRef.current = null
    }
  }, [setActiveContract, userId, tradingMode])
}

/** 广播合约变更到其他标签页 */
export function broadcastContractChange(code: string): void {
  const user = useAuthStore.getState().user
  if (!user || !isFuturesSymbol(code) || typeof BroadcastChannel === "undefined") return
  const channel = new BroadcastChannel(`qihuo-sync:${user.id}:${user.trading_mode}:futures`)
  channel.postMessage({ type: "active-contract", payload: code })
  channel.close()
}
