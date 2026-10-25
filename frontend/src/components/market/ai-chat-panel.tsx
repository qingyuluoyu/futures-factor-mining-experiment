"use client"

import { useEffect, useRef, useState } from "react"
import { X, Send, SquarePen, History, Loader2, ImageIcon, Upload } from "lucide-react"
import { useAIChatStore } from "@/stores/ai-chat"
import { useAISettingsStore } from "@/stores/ai-settings"
import { request, uploadKnowledgeDocument } from "@/lib/api"
import { ModelSelector } from "@/components/ai/model-selector"
import { MessageBubble } from "@/components/ai/message-renderer"
import { ChatHistory } from "@/components/ai/chat-history"
import { useAuthStore } from "@/stores/auth"
import { captureAiScope } from "@/lib/ai-client-scope"

interface AiChatPanelProps {
  onClose: () => void
}

/** 快捷建议 */
const SUGGESTIONS = [
  "分析当前品种走势",
  "查看最新新闻",
  "计算技术指标",
  "帮我分析支撑位和阻力位",
]
const PLAIN_SUGGESTIONS = ["解释什么是最大回撤", "说明回测中的未来函数", "股票与期货的风险有什么不同", "怎样理解模拟交易的局限"]

/** AI 聊天面板 —— 完整交互版 */
export function AiChatPanel({ onClose }: AiChatPanelProps): React.JSX.Element {
  const [input, setInput] = useState("")
  const [knowledgeUploading, setKnowledgeUploading] = useState(false)
  const [notice, setNotice] = useState("")
  const scopeKey = useAuthStore((state) => captureAiScope(state)?.key ?? null)
  const scopeRevision = useAIChatStore((state) => state.scopeRevision)
  const isCurrent = () => !!scopeKey && captureAiScope(useAuthStore.getState())?.key === scopeKey && useAIChatStore.getState().scopeRevision === scopeRevision

  const messages = useAIChatStore((s) => s.messages)
  const isStreaming = useAIChatStore((s) => s.isStreaming)
  const selectedModel = useAIChatStore((s) => s.selectedModel)
  const conversationId = useAIChatStore((s) => s.conversationId)
  const historyOpen = useAIChatStore((s) => s.historyOpen)
  const pendingImages = useAIChatStore((s) => s.pendingImages)
  const resumableTask = useAIChatStore((s) => s.resumableTask)
  const sendMessage = useAIChatStore((s) => s.sendMessage)
  const chatMode = useAIChatStore((s) => s.chatMode)
  const setChatMode = useAIChatStore((s) => s.setChatMode)
  const cooldownUntil = useAIChatStore((s) => s.cooldownUntil)
  const lastRequestId = useAIChatStore((s) => s.lastRequestId)
  const [clock, setClock] = useState(Date.now)
  const cooldownSeconds = Math.max(0, Math.ceil((cooldownUntil - clock) / 1000))
  useEffect(() => {
    setClock(Date.now())
    if (cooldownUntil <= Date.now()) return
    const timer = setInterval(() => setClock(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [cooldownUntil])
  const cancelStream = useAIChatStore((s) => s.cancelStream)
  const newConversation = useAIChatStore((s) => s.newConversation)
  const toggleHistory = useAIChatStore((s) => s.toggleHistory)
  const loadConversations = useAIChatStore((s) => s.loadConversations)
  const addPendingImage = useAIChatStore((s) => s.addPendingImage)
  const removePendingImage = useAIChatStore((s) => s.removePendingImage)
  const hasMoreMessages = useAIChatStore((s) => s.hasMoreMessages)
  const loadMoreMessages = useAIChatStore((s) => s.loadMoreMessages)

  const models = useAISettingsStore((s) => s.models)
  const fetchModels = useAISettingsStore((s) => s.fetchModels)

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const knowledgeInputRef = useRef<HTMLInputElement>(null)
  const isAtBottomRef = useRef(true)

  // 初始化：加载模型和对话列表
  useEffect(() => {
    setInput("")
    setNotice("")
    setKnowledgeUploading(false)
    if (!scopeKey) return
    const fail = () => {
      if (captureAiScope(useAuthStore.getState())?.key === scopeKey && useAIChatStore.getState().scopeRevision === scopeRevision) setNotice("AI配置或历史未能加载，请重试")
    }
    void fetchModels().catch(fail)
    void loadConversations().catch(fail)
  }, [fetchModels, loadConversations, scopeKey, scopeRevision])

  // 自动滚动到底部
  useEffect(() => {
    if (isAtBottomRef.current && messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: "smooth" })
    }
  }, [messages])

  // 检测滚动位置
  function handleScroll() {
    const el = scrollContainerRef.current
    if (!el) return
    isAtBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 50
    // 滚动到顶部时加载更多历史消息
    if (el.scrollTop < 50 && hasMoreMessages) {
      void loadMoreMessages().catch(() => { if (isCurrent()) setNotice("更早消息加载失败，请重试") })
    }
  }

  // 发送消息
  async function handleSend() {
    const text = input.trim()
    if (!isCurrent() || (!text && pendingImages.length === 0) || isStreaming || Date.now() < cooldownUntil) return
    if (chatMode === "plain" && pendingImages.length) {
      setNotice("普通对话仅支持文本，请移除图片或切换工具分析模式。")
      return
    }
    setInput("")
    await sendMessage(text || "请分析这张图片")
  }

  // 键盘事件
  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  // 点击建议
  function handleSuggestion(text: string) {
    setInput(text)
    textareaRef.current?.focus()
  }

  // 处理图片文件，转为 base64 data URL
  function processImageFile(file: File) {
    if (!file.type.startsWith("image/")) return
    if (file.size > 5 * 1024 * 1024) return  // 5MB 限制
    const reader = new FileReader()
    reader.onload = (e) => {
      const dataUrl = e.target?.result as string
      if (dataUrl && isCurrent()) addPendingImage(dataUrl)
    }
    reader.readAsDataURL(file)
  }

  // 粘贴事件：检测图片
  function handlePaste(e: React.ClipboardEvent) {
    const items = e.clipboardData.items
    for (let i = 0; i < items.length; i++) {
      if (items[i].type.startsWith("image/")) {
        e.preventDefault()
        const file = items[i].getAsFile()
        if (file) processImageFile(file)
        return
      }
    }
  }

  // 文件选择
  function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files
    if (!files) return
    for (let i = 0; i < files.length; i++) {
      processImageFile(files[i])
    }
    e.target.value = ""
  }

  // 知识库上传
  async function handleKnowledgeUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file || !isCurrent()) return
    e.target.value = ""
    setKnowledgeUploading(true)
    try {
      const result = await uploadKnowledgeDocument(file)
      if (!isCurrent()) return
      const msg = result.ok
        ? `文档 "${result.filename}" 已上传，分为 ${result.chunks} 块，已向量化 ${result.vectorized} 块`
        : "上传失败"
      setNotice(msg)
    } catch {
      if (isCurrent()) setNotice("知识库上传未完成，请检查登录状态或网络后重试")
    } finally {
      if (isCurrent()) setKnowledgeUploading(false)
    }
  }

  // 模型库为空时显示引导
  const noModels = models.length === 0

  return (
    <div className="flex h-full min-h-0 min-w-0 relative">
      {/* 对话历史侧边栏 */}
      {historyOpen && (
        <div className="absolute inset-0 z-20 flex">
          <div className="w-full sm:w-56 bg-[var(--bg-secondary)] border-r border-[var(--border)]">
            <ChatHistory />
          </div>
          <div
            className="flex-1 bg-[var(--bg-tertiary)]"
            onClick={toggleHistory}
          />
        </div>
      )}

      {/* 主面板 */}
      <div className="flex flex-col h-full min-h-0 min-w-0 flex-1">
        {notice && <p role="status" className="px-3 py-2 text-xs text-[var(--text-muted)]">{notice}</p>}
        {/* 顶部栏：模型选择 + 操作按钮 */}
        <div className="flex items-center gap-1 px-2 py-1.5 border-b border-[var(--border)]">
          <ModelSelector />
          <select aria-label="对话模式" value={chatMode} disabled={isStreaming} onChange={(e) => setChatMode(e.target.value as "plain" | "legacy")}
            className="max-w-28 rounded border border-[var(--border)] bg-[var(--bg-secondary)] p-1 text-xs">
            <option value="plain">普通对话</option>
            <option value="legacy">工具分析</option>
          </select>
          <div className="flex-1" />
          <input
            ref={knowledgeInputRef}
            type="file"
            accept=".pdf,.txt,.md,.csv"
            className="hidden"
            onChange={handleKnowledgeUpload}
          />
          <button
            onClick={() => knowledgeInputRef.current?.click()}
              disabled={knowledgeUploading || chatMode === "plain"}
            className="p-1.5 rounded hover:bg-[var(--bg-tertiary)] text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors disabled:opacity-50"
              title={chatMode === "plain" ? "普通对话不使用知识库；上传与检索请切换工具分析" : "上传知识库文档"}
          >
            {knowledgeUploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
          </button>
          <button
            onClick={() => newConversation()}
            className="p-1.5 rounded hover:bg-[var(--bg-tertiary)] text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
            title="新建对话"
          >
            <SquarePen size={14} />
          </button>
          <button
            onClick={() => { toggleHistory() }}
            className="p-1.5 rounded hover:bg-[var(--bg-tertiary)] text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
            title="对话历史"
          >
            <History size={14} />
          </button>
          <button
            onClick={onClose}
            className="p-1.5 rounded hover:bg-[var(--bg-tertiary)] text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
            title="关闭面板"
          >
            <X size={14} />
          </button>
        </div>

        <p role="status" className="px-3 py-1 text-[11px] text-[var(--text-muted)]">
          {cooldownSeconds > 0 ? `限流等待 ${cooldownSeconds} 秒，不会自动重试` : chatMode === "plain" ? "轻量文本简答 · 不读取行情/账户，不执行检索或工具" : "工具分析可能较慢并产生多次调用，交易写工具仍不可用"}
        </p>
        {lastRequestId && <div className="px-3 pb-1 text-[10px] text-[var(--text-muted)] [overflow-wrap:anywhere]">
          请求：{lastRequestId} · 取消或断网不保证供应商停止计费。
          <button type="button" className="ml-1 underline" onClick={() => {
            const captured = scopeKey
            void request<{ status: string; cost_state: string }>(`/api/ai/chat/requests/${lastRequestId}`).then((result) => {
              if (captureAiScope(useAuthStore.getState())?.key === captured) setNotice(`请求状态：${result.status}；实际费用：${result.cost_state === "unknown" ? "未知" : result.cost_state}。不会自动重发。`)
            }).catch(() => { if (captureAiScope(useAuthStore.getState())?.key === captured) setNotice("请求状态暂时无法读取，请保留编号；不会自动重发。") })
          }}>查询状态</button>
        </div>}

        {/* 消息区 */}
        <div
          ref={scrollContainerRef}
          onScroll={handleScroll}
          className="flex-1 min-h-0 min-w-0 overflow-y-auto overflow-x-hidden"
        >
          {messages.length === 0 ? (
            /* 空状态 */
            <div className="flex flex-col items-center justify-center h-full px-6 gap-4">
              <div className="text-2xl">🤖</div>
              <div className="text-sm font-medium text-[var(--text-primary)]">
                {chatMode === "plain" ? "你好，我是闻苑对话助手" : "你好，我是 AI 交易助手"}
              </div>
              <div className="text-xs text-[var(--text-muted)] text-center">
                {chatMode === "plain" ? "可以解释金融概念；不会查询当前行情、账户或执行交易" : "我可以帮你分析行情、解读新闻、计算技术指标"}
              </div>
              <div className="flex flex-col gap-2 w-full max-w-[240px]">
                {(chatMode === "plain" ? PLAIN_SUGGESTIONS : SUGGESTIONS).map((s) => (
                  <button
                    key={s}
                    onClick={() => handleSuggestion(s)}
                    className="w-full px-3 py-2 text-xs text-left rounded border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:border-[var(--accent-info)] hover:bg-[var(--bg-tertiary)] transition-colors"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            /* 消息列表 */
            <>
              {messages.map((msg) => (
                <MessageBubble key={msg.id} message={msg} />
              ))}
              {/* 任务恢复提示 */}
              {chatMode === "legacy" && resumableTask && (
                <div className="px-3 py-2">
                  <div className="flex items-center gap-2 px-3 py-2 rounded-lg border border-[var(--accent-warning)] bg-[var(--bg-tertiary)]">
                    <span className="text-xs text-[var(--text-muted)]">上次任务未完成，是否继续？</span>
                    <button
                      onClick={() => sendMessage("请继续完成上次的任务")}
                      className="px-2 py-1 text-xs rounded bg-[var(--accent-info)] text-[var(--text-primary)] hover:opacity-80 transition-opacity"
                    >
                      继续
                    </button>
                    <button
                      onClick={() => useAIChatStore.setState({ resumableTask: false })}
                      className="px-2 py-1 text-xs rounded border border-[var(--border)] text-[var(--text-muted)] hover:bg-[var(--bg-secondary)] transition-colors"
                    >
                      忽略
                    </button>
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </>
          )}
        </div>

        {/* 底部输入区 */}
        <div className="p-2 border-t border-[var(--border)]">
          {noModels ? (
            <div className="text-xs text-[var(--text-muted)] text-center py-2">
              请先在 AI 设置中添加模型
            </div>
          ) : (
            <>
              {/* 图片预览 */}
              {pendingImages.length > 0 && (
                <div className="flex gap-1.5 mb-2 flex-wrap">
                  {pendingImages.map((img, i) => (
                    <div key={i} className="relative group">
                      <img
                        src={img}
                        alt={`图片 ${i + 1}`}
                        className="w-12 h-12 object-cover rounded border border-[var(--border)]"
                      />
                      <button
                        onClick={() => removePendingImage(i)}
                        className="absolute -top-1 -right-1 w-4 h-4 flex items-center justify-center rounded-full bg-red-500 text-[var(--text-primary)] text-[10px] opacity-0 group-hover:opacity-100 transition-opacity"
                      >
                        ×
                      </button>
                    </div>
                  ))}
                </div>
              )}
              <div className="flex items-end gap-2">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/*"
                  multiple
                  className="hidden"
                  onChange={handleFileSelect}
                />
                {selectedModel?.is_multimodal && (
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    className="p-2 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors shrink-0"
                    title="添加图片"
                  >
                    <ImageIcon size={14} />
                  </button>
                )}
                <textarea
                  ref={textareaRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  onPaste={handlePaste}
                  placeholder={selectedModel ? `向 ${selectedModel.display_name} 提问...` : "输入消息..."}
                  rows={1}
                  className="flex-1 min-w-0 resize-none px-3 py-2 text-sm rounded-lg bg-[var(--bg-tertiary)] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] outline-none focus:ring-1 focus:ring-[var(--accent-info)] max-h-24 overflow-y-auto"
                  style={{ minHeight: "32px" }}
                  disabled={isStreaming}
                />
                {isStreaming ? (
                  <button
                    onClick={cancelStream}
                    className="p-2 rounded-lg bg-red-500/20 text-red-400 hover:bg-red-500/30 transition-colors shrink-0"
                    title="取消生成"
                  >
                    <Loader2 size={14} className="animate-spin" />
                  </button>
                ) : (
                  <button
                    onClick={handleSend}
                    disabled={cooldownSeconds > 0 || (!input.trim() && pendingImages.length === 0)}
                    className="p-2 rounded-lg bg-[var(--accent-info)] text-[var(--text-primary)] disabled:opacity-30 disabled:cursor-not-allowed hover:opacity-90 transition-opacity shrink-0"
                    title="发送消息"
                  >
                    <Send size={14} />
                  </button>
                )}
              </div>
            </>
          )}
          <div className="mt-1 text-[10px] text-[var(--text-muted)] text-center">
            Enter 发送 · Shift+Enter 换行 · Ctrl+V 粘贴图片
          </div>
        </div>
      </div>
    </div>
  )
}
