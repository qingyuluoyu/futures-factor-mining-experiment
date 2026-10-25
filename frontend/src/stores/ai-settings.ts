/**
 * AI 设置状态管理 —— 渠道、模型、加载状态
 */

import { create } from "zustand"
import { useAuthStore } from "@/stores/auth"
import { captureAiScope, type AiClientScope } from "@/lib/ai-client-scope"
import type { AIProvider, AIModel, PresetProvider, ProviderTestResult } from "@/types"
import {
  getAIProviders,
  getAIModels,
  getPresetProviders,
  createAIProvider,
  updateAIProvider,
  deleteAIProvider,
  testAIProvider,
  batchCreateAIModels,
  updateAIModel,
  deleteAIModel,
} from "@/lib/api"

interface AISettingsState {
  /** 渠道列表 */
  providers: AIProvider[]
  /** 模型列表 */
  models: AIModel[]
  /** 预设渠道 */
  presets: PresetProvider[]
  /** 加载状态 */
  loadingProviders: boolean
  loadingModels: boolean
  /** 测试结果映射：providerId → result */
  testResults: Record<string, ProviderTestResult>

  /** 加载渠道列表 */
  fetchProviders: () => Promise<void>
  /** 加载模型列表 */
  fetchModels: () => Promise<void>
  /** 加载预设渠道 */
  fetchPresets: () => Promise<void>
  /** 添加渠道 */
  addProvider: (data: { name: string; api_type: string; base_url: string; api_key: string; thinking_enabled?: boolean | null }) => Promise<void>
  /** 更新渠道 */
  editProvider: (
    id: string,
    data: {
      name?: string
      api_type?: string
      base_url?: string
      api_key?: string
      is_active?: boolean
      thinking_enabled?: boolean | null
    },
  ) => Promise<void>
  /** 删除渠道 */
  removeProvider: (id: string) => Promise<void>
  /** 测试渠道 */
  testProvider: (id: string) => Promise<ProviderTestResult>
  /** 从渠道批量添加模型 */
  addModels: (providerId: string, models: Array<{ model_id: string; display_name: string; is_multimodal: boolean; is_custom: boolean; capabilities: string[] }>) => Promise<void>
  /** 更新模型 */
  editModel: (id: string, data: { display_name: string }) => Promise<void>
  /** 删除模型 */
  removeModel: (id: string) => Promise<void>
}

let scopeEpoch = 0
type SettingsScope = AiClientScope & { epoch: number }
const currentScope = (): SettingsScope | null => {
  const scope = captureAiScope(useAuthStore.getState())
  return scope ? { ...scope, epoch: scopeEpoch } : null
}
const isCurrent = (scope: SettingsScope | null) => !!scope && currentScope()?.key === scope.key && scopeEpoch === scope.epoch
const requireCurrent = (scope: SettingsScope | null) => {
  if (!isCurrent(scope)) throw new Error("登录身份或市场已变更，请重新操作")
}
let providerRead = 0
let modelRead = 0
let presetRead = 0

export const useAISettingsStore = create<AISettingsState>((set, get) => ({
  providers: [],
  models: [],
  presets: [],
  loadingProviders: false,
  loadingModels: false,
  testResults: {},

  fetchProviders: async () => {
    const scope = currentScope()
    if (!scope) return
    const revision = ++providerRead
    set({ loadingProviders: true })
    try {
      const providers = await getAIProviders()
      if (isCurrent(scope) && revision === providerRead) set({ providers })
    } finally {
      if (isCurrent(scope) && revision === providerRead) set({ loadingProviders: false })
    }
  },

  fetchModels: async () => {
    const scope = currentScope()
    if (!scope) return
    const revision = ++modelRead
    set({ loadingModels: true })
    try {
      const models = await getAIModels()
      if (isCurrent(scope) && revision === modelRead) set({ models })
    } finally {
      if (isCurrent(scope) && revision === modelRead) set({ loadingModels: false })
    }
  },

  fetchPresets: async () => {
    const scope = currentScope()
    if (!scope) return
    const revision = ++presetRead
    const presets = await getPresetProviders()
    if (isCurrent(scope) && revision === presetRead) set({ presets })
  },

  addProvider: async (data) => {
    const scope = currentScope()
    requireCurrent(scope)
    await createAIProvider(data)
    requireCurrent(scope)
    await get().fetchProviders()
  },

  editProvider: async (id, data) => {
    const scope = currentScope()
    requireCurrent(scope)
    await updateAIProvider(id, data)
    requireCurrent(scope)
    await get().fetchProviders()
  },

  removeProvider: async (id) => {
    const scope = currentScope()
    requireCurrent(scope)
    await deleteAIProvider(id)
    requireCurrent(scope)
    set((s) => ({
      providers: s.providers.filter((p) => p.id !== id),
      models: s.models.filter((m) => m.provider_id !== id),
    }))
  },

  testProvider: async (id) => {
    const scope = currentScope()
    requireCurrent(scope)
    try {
      const result = await testAIProvider(id)
      requireCurrent(scope)
      set((s) => ({ testResults: { ...s.testResults, [id]: result } }))
      return result
    } catch (err) {
      requireCurrent(scope)
      const message =
        err instanceof Error ? err.message : "测试失败，请稍后重试"
      const result: ProviderTestResult = {
        status: "error",
        message,
        models: [],
      }
      set((s) => ({ testResults: { ...s.testResults, [id]: result } }))
      return result
    }
  },

  addModels: async (providerId, models) => {
    const scope = currentScope()
    requireCurrent(scope)
    await batchCreateAIModels(providerId, models)
    requireCurrent(scope)
    await get().fetchModels()
    requireCurrent(scope)
    await get().fetchProviders()
  },

  editModel: async (id, data) => {
    const scope = currentScope()
    requireCurrent(scope)
    await updateAIModel(id, data)
    requireCurrent(scope)
    set((s) => ({
      models: s.models.map((m) =>
        m.id === id ? { ...m, display_name: data.display_name } : m
      ),
    }))
  },

  removeModel: async (id) => {
    const scope = currentScope()
    requireCurrent(scope)
    await deleteAIModel(id)
    requireCurrent(scope)
    set((s) => ({
      models: s.models.filter((m) => m.id !== id),
    }))
  },
}))

useAuthStore.subscribe((state, previous) => {
  if (captureAiScope(state)?.key === captureAiScope(previous)?.key) return
  scopeEpoch++
  providerRead++
  modelRead++
  presetRead++
  useAISettingsStore.setState({ providers: [], models: [], presets: [], testResults: {}, loadingProviders: false, loadingModels: false })
})
