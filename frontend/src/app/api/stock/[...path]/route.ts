/**
 * A 股因子研究长请求代理（catch-all）
 *
 * 与因子实验室同因：Next.js rewrites 默认约 30s socket hang-up，而
 * 因子检验/组合回测需加载全池日线（20 万行级）并做横截面计算，
 * 常超过 30s，故用 Route Handler 转发并放宽超时。
 * 透传 Authorization，支持 GET/POST。
 */

import { type NextRequest, NextResponse } from "next/server"
import { stockProxyResponse } from "@/lib/stock-proxy-response"

export const maxDuration = 300
export const dynamic = "force-dynamic"
export const runtime = "nodejs"

const PROXY_TIMEOUT_MS = 300_000

type Ctx = { params: Promise<{ path: string[] }> }

function resolveBackendUrl(): string {
  const fromEnv = process.env.BACKEND_URL
  if (fromEnv && fromEnv.trim().length > 0) {
    return fromEnv.replace(/\/$/, "")
  }
  return "http://qihuo-backend:8001"
}

async function forward(
  req: NextRequest,
  segments: string[],
  method: string,
): Promise<Response> {
  const tail = ["stock", ...segments].join("/")
  const search = req.nextUrl.search
  const url = `${resolveBackendUrl()}/api/${tail}${search}`
  const headers: Record<string, string> = {}
  const auth = req.headers.get("authorization")
  if (auth) headers.Authorization = auth
  const init: RequestInit = { method, headers }

  try {
    if (method === "POST" || method === "PATCH" || method === "PUT") {
      headers["Content-Type"] = "application/json"
      init.body = await req.text()
    }
    const upstream = await fetch(url, {
      ...init,
      signal: AbortSignal.timeout(PROXY_TIMEOUT_MS),
    })
    const body = await upstream.arrayBuffer()
    return stockProxyResponse(upstream, body)
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    const code = (err as { code?: string }).code ?? ""
    const isTimeout =
      /timeout|aborted|socket hang up|econnreset|und_err/i.test(msg) ||
      /timeout|reset|und_err/i.test(code)
    return NextResponse.json(
      {
        detail: isTimeout
          ? "因子研究请求超时或连接中断。数据量较大时可缩小起始日期或减少股票池后重试"
          : `因子研究代理失败: ${msg}`.slice(0, 280),
      },
      { status: isTimeout ? 504 : 502 },
    )
  }
}

export async function GET(req: NextRequest, ctx: Ctx): Promise<Response> {
  const { path } = await ctx.params
  return forward(req, path, "GET")
}

export async function POST(req: NextRequest, ctx: Ctx): Promise<Response> {
  const { path } = await ctx.params
  return forward(req, path, "POST")
}
