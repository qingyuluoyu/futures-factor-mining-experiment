export function stockProxyResponse(upstream: Response, body: string | ArrayBuffer): Response {
  const headers = new Headers({ "Content-Type": upstream.headers.get("content-type") ?? "application/json; charset=utf-8", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" })
  for (const name of ["X-Request-ID", "X-Research-Error", "X-Method-Version", "Content-Disposition"]) {
    const value = upstream.headers.get(name)
    if (value) headers.set(name, value)
  }
  return new Response(upstream.status === 204 ? null : body, { status: upstream.status, headers })
}
