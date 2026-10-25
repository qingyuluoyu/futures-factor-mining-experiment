/**
 * AI 聊天状态管理 —— 对话、消息、模型选择、流式状态
 */

import { create } from "zustand"
import type { AIModel, AIConversationItem, AIMessageItem, ChatBubble } from "@/types"
import { createConversation, getConversations, deleteConversation, getConversationMessages } from "@/lib/api"
import { streamChat, type MultimodalMessage, type StreamCallbacks } from "@/lib/ai-stream"
import { useIndicatorStore } from "@/stores/indicator"
import { useAuthStore } from "@/stores/auth"
import { useAISettingsStore } from "@/stores/ai-settings"
import { captureAiScope, type AiClientScope } from "@/lib/ai-client-scope"

const MAX_CONTEXT_ROUNDS = 20
const currentScope = () => captureAiScope(useAuthStore.getState())
const isCurrent = (scope: AiClientScope | null) => !!scope && currentScope()?.key === scope.key
const providerEnabled = (model: AIModel) => useAISettingsStore.getState().providers.find((provider) => provider.id === model.provider_id)?.is_active !== false
let chatRevision = 0
let conversationRead = 0
let messageRead = 0

function guardCallbacks(callbacks: StreamCallbacks, active: () => boolean): StreamCallbacks {
  return new Proxy(callbacks, {
    get(target, property) {
      const callback = Reflect.get(target, property)
      if (typeof callback !== "function") return callback
      return (...args: unknown[]) => {
        if (active()) Reflect.apply(callback, target, args)
      }
    },
  })
}

// ---------- State 类型 ----------

interface AIChatState {
  chatMode: "plain" | "legacy"
  cooldownUntil: number
  lastRequestId: string | null
  setChatMode: (mode: "plain" | "legacy") => void
  scopeRevision: number
  /** 当前选中的模型 */
  selectedModel: AIModel | null
  /** 当前对话 ID */
  conversationId: string | null
  /** 对话列表 */
  conversations: AIConversationItem[]
  hasMoreConversations: boolean
  conversationOffset: number
  loadingConversations: boolean
  historyError: string | null
  /** 当前对话的消息列表 */
  messages: ChatBubble[]
  /** 是否正在流式接收中 */
  isStreaming: boolean
  /** 用于中断 SSE 的 AbortController */
  abortController: AbortController | null
  /** 历史侧边栏是否打开 */
  historyOpen: boolean
  /** 待发送的图片（base64 data URL） */
  pendingImages: string[]
  /** 是否有可恢复的任务（上次中断在工具调用中） */
  resumableTask: boolean
  /** 是否有更多历史消息可加载 */
  hasMoreMessages: boolean
  /** 当前消息分页页码 */
  currentMessagePage: number

  // ---------- Actions ----------
  /** 选择模型 */
  selectModel: (model: AIModel) => void
  /** 从 localStorage 恢复模型选择 */
  restoreModel: (models: AIModel[]) => void
  /** 新建对话 */
  newConversation: () => Promise<void>
  /** 加载对话列表 */
  loadConversations: () => Promise<void>
  loadMoreConversations: () => Promise<void>
  /** 切换到指定对话 */
  switchConversation: (id: string) => Promise<void>
  /** 删除对话 */
  removeConversation: (id: string) => Promise<void>
  /** 发送消息 */
  sendMessage: (content: string) => Promise<void>
  /** 取消当前流式请求 */
  cancelStream: () => void
  /** 切换历史侧边栏 */
  toggleHistory: () => void
  /** 添加待发送图片 */
  addPendingImage: (dataUrl: string) => void
  /** 移除待发送图片 */
  removePendingImage: (index: number) => void
  /** 清空待发送图片 */
  clearPendingImages: () => void
  /** 加载更早的历史消息 */
  loadMoreMessages: () => Promise<void>
  /** 确认/拒绝工具执行 */
  resolveConfirmation: (bubbleId: string, callId: string, approved: boolean) => void
}

/** 生成唯一 ID */
function uid(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

/** 创建空的助手消息气泡 */
function createAssistantBubble(): ChatBubble {
  return {
    id: uid(),
    role: "assistant",
    content: "",
    thinking: "",
    toolCalls: [],
    toolResults: [],
    confirmations: [],
    subAgents: [],
    reactRound: 0,
    reactMaxRounds: 0,
    isStreaming: true,
    isCancelled: false,
    createdAt: Date.now(),
  }
}

function historyBubble(message: AIMessageItem): ChatBubble {
  const rawCalls = message.tool_calls ?? []
  const rawResults = message.tool_result ?? []
  const validCalls = Array.isArray(rawCalls) && rawCalls.every((item) => item && typeof item === "object" && typeof item.name === "string" && typeof item.call_id === "string")
  const validResults = Array.isArray(rawResults) && rawResults.every((item) => item && typeof item === "object" && typeof item.name === "string" && typeof item.call_id === "string" && typeof item.content === "string")
  return {
    ...createAssistantBubble(),
    id: message.id,
    role: message.role as "user" | "assistant",
    content: message.content,
    isStreaming: false,
    createdAt: new Date(message.created_at).getTime(),
    toolCalls: validCalls ? rawCalls.map((item) => ({ name: item.name as string, args: typeof item.args === "string" ? item.args : JSON.stringify(item.args ?? {}), callId: item.call_id as string, status: "done" as const })) : [],
    toolResults: validResults ? rawResults.map((item) => ({ name: item.name as string, content: item.content as string, callId: item.call_id as string })) : [],
    error: validCalls && validResults ? undefined : "历史工具记录格式不受支持，正文保留；不会从该记录恢复执行。",
  }
}

// ---------- Store ----------

export const useAIChatStore = create<AIChatState>((set, get) => ({
  chatMode: "plain",
  cooldownUntil: 0,
  lastRequestId: null,
  setChatMode: (mode) => {
    if (!get().isStreaming) set({ chatMode: mode })
  },
  scopeRevision: 0,
  selectedModel: null,
  conversationId: null,
  conversations: [],
  hasMoreConversations: false,
  conversationOffset: 0,
  loadingConversations: false,
  historyError: null,
  messages: [],
  isStreaming: false,
  abortController: null,
  historyOpen: false,
  pendingImages: [],
  resumableTask: false,
  hasMoreMessages: false,
  currentMessagePage: 1,

  selectModel: (model: AIModel) => {
    const scope = currentScope()
    const owned = useAISettingsStore.getState().models.find((item) => item.id === model.id)
    if (!scope || !owned || !providerEnabled(owned)) return
    localStorage.removeItem("ai:selected-model")
    localStorage.setItem(scope.preferenceKey, owned.id)
    set({ selectedModel: owned })
  },

  restoreModel: (models: AIModel[]) => {
    const scope = currentScope()
    localStorage.removeItem("ai:selected-model")
    const owned = useAISettingsStore.getState().models
    const available = models.filter((item) => owned.some((current) => current.id === item.id) && providerEnabled(item))
    const savedId = scope ? localStorage.getItem(scope.preferenceKey) : null
    set({ selectedModel: scope ? available.find((item) => item.id === savedId) ?? available[0] ?? null : null })
  },

  newConversation: async () => {
    const scope = currentScope()
    if (!scope) return
    get().cancelStream()
    const revision = ++chatRevision
    set({ conversationId: null, messages: [], hasMoreMessages: false, currentMessagePage: 1, resumableTask: false })
    const { selectedModel } = get()
    const modelId = selectedModel?.model_id ?? ""
    const conv = await createConversation(modelId)
    if (!isCurrent(scope) || revision !== chatRevision) return
    set((s) => ({
      conversationId: conv.id,
      messages: [],
      conversations: [conv, ...s.conversations],
      historyOpen: false,
    }))
  },

  loadConversations: async () => {
    const scope = currentScope()
    if (!scope) return
    const revision = ++conversationRead
    set({ loadingConversations: true, historyError: null })
    try {
      const convs = await getConversations({ limit: 50, offset: 0 })
      if (isCurrent(scope) && revision === conversationRead) set({ conversations: convs, hasMoreConversations: convs.length === 50, conversationOffset: convs.length })
    } finally {
      if (isCurrent(scope) && revision === conversationRead) set({ loadingConversations: false })
    }
  },

  loadMoreConversations: async () => {
    const scope = currentScope()
    const { conversationOffset, hasMoreConversations, loadingConversations } = get()
    if (!scope || !hasMoreConversations || loadingConversations || conversationOffset > 10000) return
    const revision = ++conversationRead
    set({ loadingConversations: true })
    try {
      const convs = await getConversations({ limit: 50, offset: conversationOffset })
      if (!isCurrent(scope) || revision !== conversationRead) return
      set((state) => ({
        conversations: [...state.conversations, ...convs.filter((item) => !state.conversations.some((existing) => existing.id === item.id))],
        conversationOffset: conversationOffset + convs.length,
        hasMoreConversations: convs.length === 50 && conversationOffset + convs.length <= 10000,
      }))
    } finally {
      if (isCurrent(scope) && revision === conversationRead) set({ loadingConversations: false })
    }
  },

  switchConversation: async (id: string) => {
    const scope = currentScope()
    if (!scope) return
    get().cancelStream()
    const revision = ++chatRevision
    set({ conversationId: null, messages: [], hasMoreMessages: false, currentMessagePage: 1, resumableTask: false })
    const page = await getConversationMessages(id, 1, 50)
    if (!isCurrent(scope) || revision !== chatRevision) return
    const bubbles: ChatBubble[] = page.items
      .filter((m) => m.role === "user" || m.role === "assistant")
      .map(historyBubble)

    const lastBubble = bubbles[bubbles.length - 1]
    const resumable = !!(lastBubble?.role === "assistant" && lastBubble.toolCalls.length > 0 && lastBubble.content.length < 20)

    set({
      conversationId: id,
      messages: bubbles,
      historyOpen: false,
      resumableTask: resumable,
      hasMoreMessages: page.total > 50,
      currentMessagePage: 1,
    })
  },

  removeConversation: async (id: string) => {
    const scope = currentScope()
    const scopeRevision = get().scopeRevision
    if (!scope) return
    await deleteConversation(id)
    if (!isCurrent(scope) || get().scopeRevision !== scopeRevision) return
    const { conversationId, conversations } = get()
    const remaining = conversations.filter((c) => c.id !== id)
    set({ conversations: remaining })
    // 如果删除的是当前对话，切换到最近的或新建
    if (conversationId === id) {
      if (remaining.length > 0) {
        await get().switchConversation(remaining[0].id)
      } else {
        await get().newConversation()
      }
    }
  },

  sendMessage: async (content: string) => {
    if (Date.now() < get().cooldownUntil) return
    if (get().chatMode === "plain" && get().pendingImages.length) return
    const scope = currentScope()
    if (!scope || scope.market !== "futures") return
    const { selectedModel, conversationId, messages, isStreaming, pendingImages } = get()
    if (!selectedModel || isStreaming) return
    const revision = ++chatRevision
    set({ isStreaming: true })

    // 确保有对话
    let convId = conversationId
    if (!convId) {
      try {
        const conv = await createConversation(selectedModel.model_id)
        if (!isCurrent(scope) || revision !== chatRevision) return
        convId = conv.id
        set((s) => ({
          conversationId: convId,
          conversations: [conv, ...s.conversations],
        }))
      } catch {
        if (!isCurrent(scope) || revision !== chatRevision) return
        set({ isStreaming: false, messages: [...messages, { ...createAssistantBubble(), content: "❌ 无法创建对话，请检查登录状态或网络后重试；尚未调用模型。", isStreaming: false }] })
        return
      }
    }

    // 构建多模态内容
    const hasImages = pendingImages.length > 0
    const multimodalContent = hasImages
      ? [
          { type: "text" as const, text: content },
          ...pendingImages.map((url) => ({
            type: "image_url" as const,
            image_url: { url },
          })),
        ]
      : content

    // 添加用户消息
    const userBubble: ChatBubble = {
      id: uid(),
      role: "user",
      content: hasImages ? `[图片 x${pendingImages.length}] ${content}` : content,
      thinking: "",
      toolCalls: [],
      toolResults: [],
      confirmations: [],
      subAgents: [],
      reactRound: 0,
      reactMaxRounds: 0,
      isStreaming: false,
      isCancelled: false,
      createdAt: Date.now(),
    }

    // 添加助手占位
    const assistantBubble = createAssistantBubble()
    const newMessages = [...messages, userBubble, assistantBubble]

    set({ messages: newMessages, isStreaming: true, pendingImages: [] })

    // 构建上下文（最近 20 轮 = 40 条消息）
    const contextMessages = buildContext(newMessages)

    // 最后一条用户消息替换为多模态内容
    if (hasImages && contextMessages.length > 0) {
      contextMessages[contextMessages.length - 1] = {
        role: "user",
        content: multimodalContent as MultimodalMessage["content"],
      }
    }

    // 创建 AbortController
    const controller = new AbortController()
    set({ abortController: controller })

    const assistantId = assistantBubble.id
    const requestId = get().chatMode === "plain" ? globalThis.crypto?.randomUUID?.() : undefined
    set({ lastRequestId: requestId ?? null })

    const indicatorConfig = useIndicatorStore.getState().config
    await streamChat(
      selectedModel.model_id,
      contextMessages,
      guardCallbacks({
        onMessage: (chunk) => {
          if (!chunk) return
          set((s) => ({
            messages: s.messages.map((m) =>
              m.id === assistantId ? { ...m, content: m.content + chunk } : m
            ),
          }))
        },
        onThinking: (chunk) => {
          if (!chunk) return
          set((s) => ({
            messages: s.messages.map((m) =>
              m.id === assistantId ? { ...m, thinking: m.thinking + chunk } : m
            ),
          }))
        },
        // 同 callId 合并（OpenAI 先推 name 再推完整 args；Claude 参数增量）
        onToolCall: (name, args, callId) => {
          set((s) => ({
            messages: s.messages.map((m) => {
              if (m.id !== assistantId) return m
              const existingIdx = m.toolCalls.findIndex(
                (tc) =>
                  (callId && tc.callId === callId) ||
                  (!callId && tc.name === name && tc.status === "loading"),
              )
              if (existingIdx >= 0) {
                const next = [...m.toolCalls]
                const prev = next[existingIdx]
                next[existingIdx] = {
                  ...prev,
                  name: name || prev.name,
                  args: args ? (prev.args && !args.startsWith("{") ? prev.args + args : args) : prev.args,
                  callId: callId || prev.callId,
                  status: "loading",
                }
                return { ...m, toolCalls: next }
              }
              return {
                ...m,
                toolCalls: [
                  ...m.toolCalls,
                  {
                    name: name || "tool",
                    args: args || "",
                    callId: callId || `tmp-${Date.now()}`,
                    status: "loading" as const,
                  },
                ],
              }
            }),
          }))
        },
        onToolResult: (name, result, callId) => {
          // Agent 修改图表指标：应用 apply_config 到 indicator store
          if (
            name === "update_indicator_settings" ||
            name === "get_indicator_settings"
          ) {
            try {
              const parsed = JSON.parse(result) as {
                apply_config?: unknown
                success?: boolean
              }
              if (
                name === "update_indicator_settings" &&
                parsed?.success &&
                parsed.apply_config
              ) {
                useIndicatorStore
                  .getState()
                  .applyConfig(
                    parsed.apply_config as Record<string, unknown>,
                  )
              }
            } catch {
              // 非 JSON 则忽略
            }
          }
          set((s) => ({
            messages: s.messages.map((m) => {
              if (m.id !== assistantId) return m
              const already = m.toolResults.some((tr) => tr.callId === callId)
              return {
                ...m,
                toolResults: already
                  ? m.toolResults
                  : [...m.toolResults, { name, content: result, callId }],
                toolCalls: m.toolCalls.map((tc) =>
                  tc.callId === callId || (!callId && tc.name === name && tc.status === "loading")
                    ? { ...tc, status: "done" as const, callId: callId || tc.callId }
                    : tc
                ),
              }
            }),
          }))
        },
        onConfirmationRequest: (name, args, callId) => {
          set((s) => ({
            messages: s.messages.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    confirmations: [
                      ...m.confirmations,
                      { name, args, callId, resolved: false },
                    ],
                  }
                : m
            ),
          }))
        },
        onReactRound: (round, maxRounds) => {
          set((s) => ({
            messages: s.messages.map((m) =>
              m.id === assistantId
                ? { ...m, reactRound: round, reactMaxRounds: maxRounds }
                : m
            ),
          }))
        },
        onSubagentPlan: (agents) => {
          set((s) => ({
            messages: s.messages.map((m) => {
              if (m.id !== assistantId) return m
              return {
                ...m,
                subAgents: agents.map((a) => ({
                  id: a.id,
                  name: a.name,
                  status: (a.status as ChatBubble["subAgents"][0]["status"]) || "pending",
                  symbols: a.symbols,
                  detail: a.symbols?.length
                    ? `${a.symbols.length} 个品种`
                    : undefined,
                })),
              }
            }),
          }))
        },
        onSubagentUpdate: (payload) => {
          set((s) => ({
            messages: s.messages.map((m) => {
              if (m.id !== assistantId) return m
              const idx = m.subAgents.findIndex((a) => a.id === payload.id)
              const nextItem = {
                id: payload.id,
                name: payload.name || payload.id,
                status: (payload.status as ChatBubble["subAgents"][0]["status"]) || "pending",
                detail: payload.detail,
                progress: payload.progress,
                longCount: payload.longCount,
                shortCount: payload.shortCount,
                error: payload.error,
              }
              if (idx < 0) {
                return { ...m, subAgents: [...m.subAgents, nextItem] }
              }
              const next = [...m.subAgents]
              next[idx] = { ...next[idx], ...nextItem }
              return { ...m, subAgents: next }
            }),
          }))
        },
        onError: (msg, details) => {
          const waiting = details?.retry_after_seconds ?? (details?.error_code === "provider_rate_limited" ? 15 : 0)
          const display = `${msg}${waiting ? `\n至少等待 ${waiting} 秒后手动重试；不会自动重发。` : ""}${details?.request_id ? `\n请求编号：${details.request_id}` : ""}`
          set((s) => ({
            messages: s.messages.map((m) =>
              m.id === assistantId
                ? { ...m, error: display, isStreaming: false, toolCalls: m.toolCalls.map((tool) => tool.status === "loading" ? { ...tool, status: "error" as const } : tool) }
                : m
            ),
            isStreaming: false,
            abortController: null,
            cooldownUntil: waiting ? Date.now() + waiting * 1000 : s.cooldownUntil,
          }))
        },
        onDone: () => {
          set((s) => ({
            messages: s.messages.map((m) =>
              m.id === assistantId ? { ...m, isStreaming: false } : m
            ),
            isStreaming: false,
            abortController: null,
          }))
          void get().loadConversations().catch(() => {
            if (isCurrent(scope) && revision === chatRevision) set({ historyError: "历史列表刷新失败，旧列表可能过期，请重新打开聊天重试" })
          })
        },
      }, () => isCurrent(scope) && revision === chatRevision && !controller.signal.aborted && get().abortController === controller),
      {
        conversationId: convId,
        signal: controller.signal,
        indicatorConfig,
        accessToken: scope.accessToken,
        modelRecordId: selectedModel.id,
        chatMode: get().chatMode,
        requestId,
      },
    )
  },

  cancelStream: () => {
    const { abortController, messages } = get()
    chatRevision++
    abortController?.abort()
    set({ isStreaming: false, abortController: null })
    // 标记最后一条助手消息为已取消
    const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant" && m.isStreaming)
    if (lastAssistant) {
      set((s) => ({
        messages: s.messages.map((m) =>
          m.id === lastAssistant.id ? { ...m, isStreaming: false, isCancelled: true } : m
        ),
        isStreaming: false,
        abortController: null,
      }))
    }
  },

  toggleHistory: () => {
    set((s) => ({ historyOpen: !s.historyOpen }))
  },

  resolveConfirmation: (bubbleId: string, callId: string, approved: boolean) => {
    set((s) => ({
      messages: s.messages.map((m) => {
        if (m.id !== bubbleId) return m
        return {
          ...m,
          confirmations: m.confirmations.map((c) =>
            c.callId === callId ? { ...c, resolved: true } : c
          ),
          content: approved
            ? m.content + "\n\n> 未执行：服务端逐笔确认尚未开放，请前往模拟交易页面手动操作。"
            : m.content + `\n\n> 用户已拒绝执行 ${m.confirmations.find((c) => c.callId === callId)?.name ?? "工具"}`,
        }
      }),
    }))
  },

  addPendingImage: (dataUrl: string) => {
    set((s) => ({ pendingImages: [...s.pendingImages, dataUrl] }))
  },

  removePendingImage: (index: number) => {
    set((s) => ({ pendingImages: s.pendingImages.filter((_, i) => i !== index) }))
  },

  clearPendingImages: () => {
    set({ pendingImages: [] })
  },

  loadMoreMessages: async () => {
    const scope = currentScope()
    const revision = chatRevision
    const read = ++messageRead
    const { conversationId, currentMessagePage, hasMoreMessages } = get()
    if (!scope || !conversationId || !hasMoreMessages) return

    const nextPage = currentMessagePage + 1
    const page = await getConversationMessages(conversationId, nextPage, 50)
    if (!isCurrent(scope) || revision !== chatRevision || read !== messageRead || get().conversationId !== conversationId) return

    const olderBubbles: ChatBubble[] = page.items
      .filter((m) => m.role === "user" || m.role === "assistant")
      .map(historyBubble)

    if (olderBubbles.length === 0) {
      set({ hasMoreMessages: false })
      return
    }

    set({
      messages: [...olderBubbles.filter((older) => !get().messages.some((item) => item.id === older.id)), ...get().messages],
      currentMessagePage: nextPage,
      hasMoreMessages: page.total > nextPage * 50,
    })
  },
}))

useAuthStore.subscribe((state, previous) => {
  if (captureAiScope(state)?.key === captureAiScope(previous)?.key) return
  chatRevision++
  conversationRead++
  messageRead++
  useAIChatStore.getState().abortController?.abort()
  useAIChatStore.setState({ chatMode: "plain", cooldownUntil: 0, lastRequestId: null })
  useAIChatStore.setState({ scopeRevision: useAIChatStore.getState().scopeRevision + 1, selectedModel: null, conversationId: null, conversations: [], hasMoreConversations: false, conversationOffset: 0, loadingConversations: false, historyError: null, messages: [], isStreaming: false, abortController: null, historyOpen: false, pendingImages: [], resumableTask: false, hasMoreMessages: false, currentMessagePage: 1 })
})

useAISettingsStore.subscribe((state, previous) => {
  const selected = useAIChatStore.getState().selectedModel
  if (!selected) return
  const removed = previous.models.some((model) => model.id === selected.id) && !state.models.some((model) => model.id === selected.id)
  if (!removed && providerEnabled(selected)) return
  useAIChatStore.getState().cancelStream()
  useAIChatStore.setState({ selectedModel: null })
})

/** 构建上下文消息列表（最近 20 轮） */
function buildContext(messages: ChatBubble[]): Array<MultimodalMessage> {
  const usable = messages.filter((message) => message.role === "user" || (!message.isStreaming && !message.isCancelled && !message.error && message.content && !message.content.startsWith("❌")))
  const userIndices = usable.flatMap((message, index) => message.role === "user" ? [index] : [])
  const start = userIndices[Math.max(0, userIndices.length - MAX_CONTEXT_ROUNDS)] ?? 0
  return usable.slice(start).map((message) => ({ role: message.role, content: message.content }))
}
