/**
 * 因子实验室长请求代理（catch-all）
 *
 * Next.js rewrites 默认约 30s socket hang-up；因子搜索（含 LLM 教练）
 * 与 LLM 生成常需 1–3 分钟，故用 Route Handler 转发并放宽超时。
 * 透传 Authorization，支持 GET/POST/PATCH/DELETE。
 */

import { type NextRequest, NextResponse } from "next/server"

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
  const tail = ["factor-lab", ...segments].join("/")
  const search = req.nextUrl.search
  const url = `${resolveBackendUrl()}/api/${tail}${search}`
  const headers: Record<string, string> = {}
  const auth = req.headers.get("authorization")
  if (auth) headers.Authorization = auth
  const init: RequestInit = { method, headers }

  // 整个转发流程（含 body 读取）都包在 try 内：任何环节异常都返回可读 JSON，
  // 避免抛成 Next.js 框架层的裸 500（前端只能显示「请求失败」无法定位）。
  try {
    if (method === "POST" || method === "PATCH" || method === "PUT") {
      headers["Content-Type"] = "application/json"
      init.body = await req.text()
    }
    const upstream = await fetch(url, {
      ...init,
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
    const code = (err as { code?: string }).code ?? ""
    // 超时、连接重置、socket hang up 都视为上游处理过久（LLM 多轮调用常见）
    const isTimeout =
      /timeout|aborted|socket hang up|econnreset|und_err/i.test(msg) ||
      /timeout|reset|und_err/i.test(code)
    return NextResponse.json(
      {
        detail: isTimeout
          ? "因子实验室请求超时或连接中断。LLM 生成耗时较长，请换更快的模型、缩小候选数或降低周期数据量后重试"
          : `因子实验室代理失败: ${msg}`.slice(0, 280),
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

export async function PATCH(req: NextRequest, ctx: Ctx): Promise<Response> {
  const { path } = await ctx.params
  return forward(req, path, "PATCH")
}

export async function DELETE(req: NextRequest, ctx: Ctx): Promise<Response> {
  const { path } = await ctx.params
  return forward(req, path, "DELETE")
}
