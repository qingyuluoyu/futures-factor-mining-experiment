/**
 * AI 生成 K 线 · step 决策代理
 *
 * 单根决策很快（量化<50ms，AI<10s），超时设 60s 覆盖 AI 策略。
 */

import { type NextRequest, NextResponse } from "next/server"

export const maxDuration = 60
export const dynamic = "force-dynamic"
export const runtime = "nodejs"

const PROXY_TIMEOUT_MS = 60_000

function resolveBackendUrl(): string {
  const fromEnv = process.env.BACKEND_URL
  if (fromEnv && fromEnv.trim().length > 0) {
    return fromEnv.replace(/\/$/, "")
  }
  return "http://qihuo-backend:8001"
}

/** POST /api/backtest/step → 后端边推边决策 */
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
    const upstream = await fetch(`${backend}/api/backtest/step`, {
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
          ? "决策超时。AI 策略可换更快的模型或改用量化策略"
          : `决策代理失败: ${msg}`.slice(0, 280),
      },
      { status: isTimeout ? 504 : 502 },
    )
  }
}
