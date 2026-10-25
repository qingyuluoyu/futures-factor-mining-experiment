/**
 * AI 生成 K 线 · finish 指标计算代理
 *
 * 后端 compute_metrics 很快，超时设 30s 足够。
 */

import { type NextRequest, NextResponse } from "next/server"

export const maxDuration = 30
export const dynamic = "force-dynamic"
export const runtime = "nodejs"

const PROXY_TIMEOUT_MS = 30_000

function resolveBackendUrl(): string {
  const fromEnv = process.env.BACKEND_URL
  if (fromEnv && fromEnv.trim().length > 0) {
    return fromEnv.replace(/\/$/, "")
  }
  return "http://qihuo-backend:8001"
}

/** POST /api/backtest/finish → 后端算最终指标 */
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
    const upstream = await fetch(`${backend}/api/backtest/finish`, {
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
    return NextResponse.json(
      { detail: `指标计算代理失败: ${msg}`.slice(0, 280) },
      { status: 502 },
    )
  }
}
