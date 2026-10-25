"use client"

/** 超级因子挖掘 —— 任务列表 + 进度轮询 hook */

import { useCallback, useEffect, useState } from "react"
import {
  POLL_INTERVAL_MS,
  cancelTask as apiCancel,
  createTask as apiCreate,
  deleteTask as apiDelete,
  getChampions as apiGetChampions,
  listTasks as apiList,
  pauseTask as apiPause,
  resumeTask as apiResume,
  type CreateTaskPayload,
  type MiningTask,
} from "@/lib/super-factor-api"
import type { Champion } from "@/lib/factor-lab-api"

export function useMiningTasks() {
  const [tasks, setTasks] = useState<MiningTask[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const list = await apiList()
      setTasks(list)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载任务失败")
    }
  }, [])

  // 初次加载
  useEffect(() => {
    void refresh()
  }, [refresh])

  // 有运行中任务时高频轮询，否则低频
  const hasActive = tasks.some(
    (t) => t.status === "running" || t.status === "pending",
  )
  useEffect(() => {
    const interval = hasActive ? POLL_INTERVAL_MS : 30_000
    const timer = setInterval(() => void refresh(), interval)
    return () => clearInterval(timer)
  }, [hasActive, refresh])

  const createTask = useCallback(
    async (payload: CreateTaskPayload): Promise<void> => {
      setLoading(true)
      setError(null)
      try {
        await apiCreate(payload)
        await refresh()
      } catch (e) {
        setError(e instanceof Error ? e.message : "创建任务失败")
        throw e
      } finally {
        setLoading(false)
      }
    },
    [refresh],
  )

  const pauseTask = useCallback(
    async (id: string) => {
      try {
        await apiPause(id)
        await refresh()
      } catch (e) {
        setError(e instanceof Error ? e.message : "暂停失败")
      }
    },
    [refresh],
  )

  const resumeTask = useCallback(
    async (id: string) => {
      try {
        await apiResume(id)
        await refresh()
      } catch (e) {
        setError(e instanceof Error ? e.message : "恢复失败")
      }
    },
    [refresh],
  )

  const cancelTask = useCallback(
    async (id: string) => {
      try {
        await apiCancel(id)
        await refresh()
      } catch (e) {
        setError(e instanceof Error ? e.message : "取消失败")
      }
    },
    [refresh],
  )

  const removeTask = useCallback(
    async (id: string) => {
      try {
        await apiDelete(id)
        await refresh()
      } catch (e) {
        setError(e instanceof Error ? e.message : "删除失败")
      }
    },
    [refresh],
  )

  return {
    tasks,
    loading,
    error,
    refresh,
    createTask,
    pauseTask,
    resumeTask,
    cancelTask,
    removeTask,
  }
}

/** 加载完成任务的冠军因子（按需） */
export function useTaskChampions(taskId: string | null) {
  const [result, setResult] = useState<{ taskId: string | null; champions: Champion[]; loading: boolean; error: string | null }>({ taskId: null, champions: [], loading: false, error: null })
  const [revision, setRevision] = useState(0)
  const retry = useCallback(() => setRevision((value) => value + 1), [])

  useEffect(() => {
    if (!taskId) {
      setResult({ taskId: null, champions: [], loading: false, error: null })
      return
    }
    let cancelled = false
    void (async () => {
      setResult({ taskId, champions: [], loading: true, error: null })
      try {
        const list = await apiGetChampions(taskId)
        if (!cancelled) setResult({ taskId, champions: list, loading: false, error: null })
      } catch (error) {
        if (!cancelled) setResult({ taskId, champions: [], loading: false, error: error instanceof Error ? error.message : "冠军结果读取失败，请重试" })
      }
    })()
    return () => {
      cancelled = true
    }
  }, [taskId, revision])

  return result.taskId === taskId
    ? { champions: result.champions, loading: result.loading, error: result.error, retry }
    : { champions: [], loading: Boolean(taskId), error: null, retry }
}
