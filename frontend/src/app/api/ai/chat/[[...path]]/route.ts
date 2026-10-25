export const runtime = "nodejs"
export const dynamic = "force-dynamic"
export const maxDuration = 300

const MAX_BYTES = 32 * 1024 * 1024
type Context = { params: Promise<{ path?: string[] }> }

async function proxy(request: Request, context: Context): Promise<Response> {
  const path = (await context.params).path ?? []
  const suffix = path.join("/")
  const valid = request.method === "POST" ? suffix === "" : suffix === "capabilities" || /^requests\/[a-fA-F0-9-]{36}$/.test(suffix)
  if (!valid) return Response.json({ detail: "路径不存在" }, { status: 404 })
  const headers = new Headers()
  for (const name of ["Authorization", "Content-Type", "Accept"]) {
    const value = request.headers.get(name)
    if (value) headers.set(name, value)
  }
  let body: ArrayBuffer | undefined
  if (request.method === "POST") {
    if (Number(request.headers.get("Content-Length")) > MAX_BYTES) return Response.json({ detail: "请求内容过大" }, { status: 413 })
    const reader = request.body?.getReader()
    const chunks: Uint8Array[] = []
    let size = 0
    if (reader) {
      while (true) {
        const chunk = await reader.read()
        if (chunk.done) break
        size += chunk.value.byteLength
        if (size > MAX_BYTES) {
          await reader.cancel()
          return Response.json({ detail: "请求内容过大" }, { status: 413 })
        }
        chunks.push(chunk.value)
      }
    }
    const joined = new Uint8Array(size)
    let offset = 0
    for (const chunk of chunks) { joined.set(chunk, offset); offset += chunk.byteLength }
    body = joined.buffer
  }
  try {
    const backend = (process.env.BACKEND_URL ?? "http://qihuo-backend:8001").replace(/\/$/, "")
    const upstream = await fetch(`${backend}/api/ai/chat${suffix ? "/" + suffix : ""}`, {
      method: request.method, headers, ...(body ? { body } : {}), cache: "no-store", redirect: "manual",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(300000)]),
    })
    if (upstream.status >= 300 && upstream.status < 400) return Response.json({ detail: "对话服务地址异常，未跟随重定向" }, { status: 502 })
    const responseHeaders = new Headers({ "Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no" })
    for (const name of ["Content-Type", "X-Request-ID", "Retry-After"]) {
      const value = upstream.headers.get(name)
      if (value) responseHeaders.set(name, value)
    }
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders })
  } catch {
    return Response.json({ detail: "对话服务连接中断，执行状态可能未知；请保留请求编号查询，不会自动重发。", error_code: "chat_proxy_unavailable" }, { status: 503 })
  }
}

export const GET = proxy
export const POST = proxy
