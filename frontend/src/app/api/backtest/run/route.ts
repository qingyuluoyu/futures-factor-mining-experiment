/**
 * 回测长请求代理
 *
 * Next.js rewrites 默认约 30s 会 socket hang up，
 * AI 回测常需 1–3 分钟，故用 Route Handler 转发并放宽超时。
 */

import { type NextRequest, NextResponse } from "next/server"

/** Node / 自托管场景允许最长执行时间（秒） */
export const maxDuration = 300
export const dynamic = "force-dynamic"
export const runtime = "nodejs"

const PROXY_TIMEOUT_MS = 300_000

/** 解析后端基址（容器内走服务名） */
function resolveBackendUrl(): string {
  const fromEnv = process.env.BACKEND_URL
  if (fromEnv && fromEnv.trim().length > 0) {
    return fromEnv.replace(/\/$/, "")
  }
  return "http://qihuo-backend:8001"
}

/** POST /api/backtest/run → 后端同步回测 */
export async function POST(request: NextRequest): Promise<Response> {
  const backend = resolveBackendUrl()
  const auth = request.headers.get("authorization")
  const bodyText = await request.text()

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  }
  if (auth) {
    headers.Authorization = auth
  }

  try {
    const upstream = await fetch(`${backend}/api/backtest/run`, {
      method: "POST",
      headers,
      body: bodyText,
      signal: AbortSignal.timeout(PROXY_TIMEOUT_MS),
    })
    const text = await upstream.text()
    const contentType =
      upstream.headers.get("content-type") ?? "application/json; charset=utf-8"
    const responseHeaders = new Headers({ "Content-Type": contentType, "Cache-Control": "no-store" })
    const requestId = upstream.headers.get("X-Request-ID")
    if (requestId) responseHeaders.set("X-Request-ID", requestId)
    return new Response(text, {
      status: upstream.status,
      headers: responseHeaders,
    })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    const isTimeout =
      msg.includes("Timeout") ||
      msg.includes("timeout") ||
      msg.includes("aborted")
    return NextResponse.json(
      {
        detail: isTimeout
          ? "回测超时（超过 5 分钟）。请缩短区间或改用量化策略后重试"
          : `回测代理失败: ${msg}`.slice(0, 280),
      },
      { status: isTimeout ? 504 : 502 },
    )
  }
}
