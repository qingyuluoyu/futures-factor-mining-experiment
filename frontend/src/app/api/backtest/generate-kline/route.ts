/**
 * AI 生成 K 线代理
 *
 * 生成需调用 LLM 产出剧本（约 5-30s），放宽到 120s。
 */

import { type NextRequest, NextResponse } from "next/server"

export const maxDuration = 120
export const dynamic = "force-dynamic"
export const runtime = "nodejs"

const PROXY_TIMEOUT_MS = 120_000

function resolveBackendUrl(): string {
  const fromEnv = process.env.BACKEND_URL
  if (fromEnv && fromEnv.trim().length > 0) {
    return fromEnv.replace(/\/$/, "")
  }
  return "http://qihuo-backend:8001"
}

/** POST /api/backtest/generate-kline → 后端生成合成 K 线 */
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
    const upstream = await fetch(`${backend}/api/backtest/generate-kline`, {
      method: "POST",
      headers,
      body: bodyText,
      signal: AbortSignal.timeout(PROXY_TIMEOUT_MS),
    })
    const text = await upstream.text()
    const contentType =
      upstream.headers.get("content-type") ?? "application/json; charset=utf-8"
    return new Response(text, {
      status: upstream.status,
      headers: { "Content-Type": contentType },
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
          ? "AI 生成超时（超过 120s）。请重试或换模型"
          : `生成代理失败: ${msg}`.slice(0, 280),
      },
      { status: isTimeout ? 504 : 502 },
    )
  }
}
