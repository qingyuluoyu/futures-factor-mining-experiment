/**
 * SSE 流式消费 —— fetch + ReadableStream，按事件分发
 *
 * 聊天 SSE 优先直连后端（NEXT_PUBLIC_WS_URL / STREAM_URL），
 * 避免经 Next rewrites 时整段缓冲导致「一瞬间出全文」。
 */

import type { StreamEvent } from "@/types"

/** 子代理 plan 项 */
export interface SubAgentPlanPayload {
  id: string
  name: string
  symbols?: string[]
  product_codes?: string[]
  status?: string
}

/** 事件回调 */
export interface StreamCallbacks {
  onMessage: (content: string) => void
  onThinking: (content: string) => void
  onToolCall: (name: string, args: string, callId: string) => void
  onToolResult: (name: string, content: string, callId: string) => void
  onConfirmationRequest: (name: string, args: string, callId: string) => void
  onReactRound: (round: number, maxRounds: number) => void
  onSubagentPlan: (agents: SubAgentPlanPayload[], total: number) => void
  onSubagentUpdate: (payload: {
    id: string
    name: string
    status: string
    detail: string
    progress?: number
    longCount?: number
    shortCount?: number
    error?: string | null
  }) => void
  onError: (message: string, details?: StreamErrorDetails) => void
  onDone: () => void
}

/** 流式请求可选参数 */
export interface StreamChatOptions {
  modelRecordId?: string
  accessToken?: string
  conversationId?: string
  signal?: AbortSignal
  /** 当前图表指标配置，供 Agent 读写 */
  indicatorConfig?: unknown
  chatMode?: "plain" | "legacy"
  requestId?: string
}

export interface StreamErrorDetails {
  error_code?: string
  retry_after_seconds?: number
  upstream_status?: number
  request_id?: string
}

function errorDetails(value: unknown, requestId?: string): StreamErrorDetails {
  const data = value && typeof value === "object" ? value as Record<string, unknown> : {}
  return {
    ...(typeof data.error_code === "string" && /^[a-z0-9_]{1,64}$/.test(data.error_code) ? { error_code: data.error_code } : {}),
    ...(Number.isInteger(data.retry_after_seconds) && Number(data.retry_after_seconds) > 0 && Number(data.retry_after_seconds) <= 86400 ? { retry_after_seconds: Number(data.retry_after_seconds) } : {}),
    ...(Number.isInteger(data.upstream_status) && Number(data.upstream_status) >= 100 && Number(data.upstream_status) <= 599 ? { upstream_status: Number(data.upstream_status) } : {}),
    ...(requestId ? { request_id: requestId } : {}),
  }
}

/** 多模态消息 */
export interface MultimodalMessage {
  role: string
  content:
    | string
    | Array<
        | { type: "text"; text: string }
        | { type: "image_url"; image_url: { url: string } }
      >
}

/**
 * 解析 SSE 请求基址
 * 1. NEXT_PUBLIC_STREAM_URL
 * 2. NEXT_PUBLIC_WS_URL（ws→http）
 * 3. NEXT_PUBLIC_API_URL
 * 4. 同域（可能被 Next 缓冲）
 */
function getStreamBase(): string {
  if (process.env.NEXT_PUBLIC_CHAT_SAME_ORIGIN === "1") return ""
  const stream = process.env.NEXT_PUBLIC_STREAM_URL?.trim()
  if (stream) return stream.replace(/\/$/, "")

  const ws = process.env.NEXT_PUBLIC_WS_URL?.trim()
  if (ws) {
    return ws.replace(/^ws/i, "http").replace(/\/$/, "")
  }

  const api = process.env.NEXT_PUBLIC_API_URL?.trim()
  if (api) return api.replace(/\/$/, "")

  return ""
}

/** 发送流式聊天并消费 SSE */
export async function streamChat(
  modelId: string,
  messages: Array<MultimodalMessage>,
  callbacks: StreamCallbacks,
  conversationIdOrOptions?: string | StreamChatOptions,
  maybeSignal?: AbortSignal,
): Promise<void> {
  // 兼容旧签名 (..., conversationId, signal) 与新签名 (..., options)
  let conversationId: string | undefined
  let signal: AbortSignal | undefined
  let indicatorConfig: unknown
  let accessToken: string | undefined
  let modelRecordId: string | undefined
  let chatMode: "plain" | "legacy" | undefined
  let requestId: string | undefined
  if (
    conversationIdOrOptions &&
    typeof conversationIdOrOptions === "object" &&
    !Array.isArray(conversationIdOrOptions)
  ) {
    conversationId = conversationIdOrOptions.conversationId
    signal = conversationIdOrOptions.signal
    indicatorConfig = conversationIdOrOptions.indicatorConfig
    accessToken = conversationIdOrOptions.accessToken
    modelRecordId = conversationIdOrOptions.modelRecordId
    chatMode = conversationIdOrOptions.chatMode
    requestId = conversationIdOrOptions.requestId
  } else {
    conversationId = conversationIdOrOptions as string | undefined
    signal = maybeSignal
  }

  const token =
    accessToken ?? (typeof window !== "undefined" ? localStorage.getItem("access_token") : null)
  const base = getStreamBase()
  const userSignal = signal
  signal = AbortSignal.any([...(signal ? [signal] : []), AbortSignal.timeout(chatMode === "plain" ? 95000 : 300000)])

  let response: Response
  try {
    if (modelRecordId) {
      let contract: { model_routing?: string; plain_chat?: string } | null
      try {
        const capability = await fetch(`${base}/api/ai/chat/capabilities`, {
          headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
          signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(10000)]) : AbortSignal.timeout(10000),
          cache: "no-store",
        })
        contract = capability.ok ? await capability.json() : null
      } catch {
        if (userSignal?.aborted) callbacks.onDone()
        else callbacks.onError("服务端模型选路校验失败，请检查登录状态或后端后重试；未调用模型")
        return
      }
      if (contract?.model_routing !== "owned-model-row-v1") {
        callbacks.onError("服务端尚未确认精确模型选路能力，请更新或恢复后端后重试；未调用模型")
        return
      }
      if (chatMode === "plain" && contract.plain_chat !== "durable-plain-v1") {
        callbacks.onError("普通对话服务尚未就绪，请稍后刷新；未调用模型", { error_code: "plain_chat_unavailable" })
        return
      }
    }
    if (signal?.aborted) {
      callbacks.onDone()
      return
    }
    response = await fetch(`${base}/api/ai/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({
        model_id: modelId,
        ...(modelRecordId ? { model_record_id: modelRecordId } : {}),
        messages,
        ...(conversationId ? { conversation_id: conversationId } : {}),
        ...(indicatorConfig != null
          ? { indicator_config: indicatorConfig }
          : {}),
        ...(chatMode ? { chat_mode: chatMode } : {}),
        ...(requestId ? { client_request_id: requestId } : {}),
      }),
      signal,
      cache: "no-store",
    })
  } catch (err) {
    if (userSignal?.aborted) {
      callbacks.onDone()
      return
    }
    callbacks.onError(signal?.aborted ? "请求超过等待时限；执行状态可能未知，请查看历史，不会自动重发。" : "无法连接对话服务，请检查网络或登录状态。", { error_code: signal?.aborted ? "chat_timeout" : "chat_connection_failed", request_id: requestId })
    return
  }

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "请求失败" }))
    const detail =
      typeof error?.detail === "string"
        ? error.detail
        : typeof error?.detail?.message === "string" ? error.detail.message
        : `请求失败: ${response.status}`
    const metadata = error && typeof error.detail === "object" ? error.detail : error
    callbacks.onError(detail, errorDetails(metadata, response.headers.get("X-Request-ID") ?? requestId))
    return
  }

  const reader = response.body?.getReader()
  if (!reader) {
    callbacks.onError("无法读取响应流")
    return
  }

  const decoder = new TextDecoder()
  let buffer = ""
  let finished = false
  let terminal = false
  const traceId = response.headers.get("X-Request-ID") ?? requestId

  const finishOnce = () => {
    if (finished) return
    finished = true
    callbacks.onDone()
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split(/\r?\n\r?\n/)
      buffer = parts.pop() ?? ""

      for (const part of parts) {
        const event = parseSseBlock(part)
        if (!event) continue
        if (event.type === "done") {
          terminal = true
          finishOnce()
          await reader.cancel()
          return
        }
        dispatchEvent(event, callbacks, traceId)
        if (event.type === "error") {
          terminal = true
          finishOnce()
          await reader.cancel()
          return
        }
      }
    }

    // 残留半包
    if (buffer.trim()) {
      const event = parseSseBlock(buffer)
      if (event) {
        terminal = event.type === "done" || event.type === "error"
        dispatchEvent(event, callbacks, traceId)
      }
    }
    if (!terminal && !userSignal?.aborted) callbacks.onError("回答流未完整结束，已收到的内容保留；不会自动重发。", { error_code: "stream_incomplete", request_id: traceId })
  } catch (err) {
    if (!userSignal?.aborted) {
      callbacks.onError(signal?.aborted ? "对话等待超时，执行状态可能未知；不会自动重发。" : "回答连接中断，部分内容已保留；不会自动重发。", { error_code: signal?.aborted ? "chat_timeout" : "stream_transport_failed", request_id: traceId })
    }
  } finally {
    finishOnce()
  }
}

function dispatchEvent(event: StreamEvent, callbacks: StreamCallbacks, traceId?: string): void {
  switch (event.type) {
    case "message":
      callbacks.onMessage(event.content ?? "")
      break
    case "thinking":
      callbacks.onThinking(event.content ?? "")
      break
    case "tool_call":
      callbacks.onToolCall(
        event.name ?? "",
        event.arguments ?? "",
        event.call_id ?? "",
      )
      break
    case "tool_result":
      callbacks.onToolResult(
        event.name ?? "",
        event.content ?? "",
        event.call_id ?? "",
      )
      break
    case "confirmation_request":
      callbacks.onConfirmationRequest(
        event.name ?? "",
        event.arguments ?? "",
        event.call_id ?? "",
      )
      break
    case "react_round":
      callbacks.onReactRound(event.round ?? 1, event.max_rounds ?? 5)
      break
    case "subagent_plan":
      callbacks.onSubagentPlan(event.agents ?? [], event.total ?? 0)
      break
    case "subagent_update":
      callbacks.onSubagentUpdate({
        id: event.id ?? "",
        name: event.name ?? "",
        status: event.status ?? "pending",
        detail: event.detail ?? "",
        progress:
          typeof event.progress === "number" ? event.progress : undefined,
        longCount:
          typeof event.long_count === "number" ? event.long_count : undefined,
        shortCount:
          typeof event.short_count === "number" ? event.short_count : undefined,
        error: event.error,
      })
      break
    case "error":
      callbacks.onError(event.message ?? "未知错误", errorDetails(event, traceId))
      break
    case "done":
      break
  }
}

/** 解析单个 SSE 事件块 */
function parseSseBlock(block: string): StreamEvent | null {
  let eventType = ""
  let dataStr = ""

  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) {
      eventType = line.slice(7).trim()
    } else if (line.startsWith("data: ")) {
      // 多行 data 拼接
      dataStr = dataStr ? `${dataStr}\n${line.slice(6)}` : line.slice(6)
    }
  }

  if (!eventType) return null

  try {
    const data = dataStr ? JSON.parse(dataStr) : {}
    if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("invalid event")
    return { ...data, type: eventType as StreamEvent["type"] }
  } catch {
    return { type: "error", message: "对话事件格式无效，未将响应认定为完成。", error_code: "stream_invalid_event" } as StreamEvent
  }
}
