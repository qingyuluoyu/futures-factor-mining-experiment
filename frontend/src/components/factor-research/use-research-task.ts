"use client"

import { useEffect, useRef, useState } from "react"
import { z } from "zod"
import { useAuthStore } from "@/stores/auth"
import { cancelStockResearchJob, createStockResearchJob, getStockResearchAvailability, getStockResearchJob, getStockResearchResult, isActiveResearchJob, newResearchCreateSchema, researchCreateSchema, stockResearchScope, type ResearchAvailability, type ResearchCreateInput, type ResearchJob, type ResearchKind, type ResearchResult } from "@/lib/stock-research-api"

const recoverySchema = z.object({ jobId: z.uuid().optional(), draft: researchCreateSchema.optional() }).strict()
type Recovery = z.infer<typeof recoverySchema>

export function useResearchTask(token: string, scope: string) {
  const sessionRevision = useAuthStore((state) => state.sessionRevision)
  const capturedSessionRevision = useRef(sessionRevision)
  const [availability, setAvailability] = useState<ResearchAvailability | null>(null)
  const [job, setJob] = useState<ResearchJob | null>(null)
  const [result, setResult] = useState<ResearchResult | null>(null)
  const [error, setError] = useState("")
  const [storageError, setStorageError] = useState("")
  const [busy, setBusy] = useState(false)
  const [blocked, setBlocked] = useState(false)
  const [recoveryPending, setRecoveryPending] = useState(true)
  const [cancelling, setCancelling] = useState(false)
  const [refreshingAvailability, setRefreshingAvailability] = useState(false)
  const mounted = useRef(false)
  const controller = useRef<AbortController | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const recovery = useRef<Recovery>({})
  const recoveryReady = useRef(false)
  const recoveryReadFailed = useRef(false)
  const activeJob = useRef<ResearchJob | null>(null)
  const inflight = useRef(false)
  const cancelRequested = useRef(false)
  const availabilityInFlight = useRef(false)
  const pollCount = useRef(0)
  const pollStartedAt = useRef(Date.now())
  const storageKey = `stock-research:${scope}`
  const current = () => {
    const auth = useAuthStore.getState()
    return mounted.current && auth.accessToken === token && auth.sessionRevision === capturedSessionRevision.current && auth.user?.market_type === "stock" && stockResearchScope(auth.user.id, token) === scope
  }
  const currentRequest = (requestController: AbortController | null) => current() && requestController === controller.current && requestController !== null && !requestController.signal.aborted
  const stopTimer = () => { if (timer.current !== null) clearTimeout(timer.current); timer.current = null }
  const persist = (value: Recovery) => {
    recovery.current = value
    try { localStorage.setItem(storageKey, JSON.stringify(value)) }
    catch { if (current()) setStorageError("浏览器无法保存任务恢复记录，请记录任务ID并从历史报告查询。") }
  }
  const fail = (failure: unknown, requestController = controller.current) => {
    if (!currentRequest(requestController)) return
    stopTimer()
    setBusy(false)
    setError(failure instanceof Error ? failure.message : "研究请求失败，请手动重试")
    if ((failure as { status?: number })?.status === 404) {
      persist({})
      activeJob.current = null
      setJob(null)
      setResult(null)
      setBlocked(false)
      recoveryReady.current = true
      setRecoveryPending(false)
    }
  }
  const acceptJob = async (value: ResearchJob, requestController: AbortController | null) => {
    if (!currentRequest(requestController)) return
    activeJob.current = value
    recoveryReady.current = true
    setRecoveryPending(false)
    setJob(value)
    persist({ jobId: value.id })
    const active = isActiveResearchJob(value)
    setBlocked(active)
    setBusy(active)
    if (value.status === "completed") {
      const report = await getStockResearchResult(value.id, value.kind, token, requestController?.signal)
      if (currentRequest(requestController) && activeJob.current?.id === value.id) setResult(report)
    }
    if (!currentRequest(requestController)) return
    if (value.error) setError(`${value.error.code}: ${value.error.message}`)
    if (active) {
      if (++pollCount.current > 540 || Date.now() - pollStartedAt.current >= 18 * 60 * 1000) { setBusy(false); setError("自动查询已达次数或18分钟时间上限，请手动查询任务状态。停止查询不会取消后台任务。"); return }
      stopTimer()
      timer.current = setTimeout(() => { timer.current = null; void refreshJob(value.id) }, 2000)
    }
  }
  const sendCancel = async () => {
    if (!current() || inflight.current) return
    if (!activeJob.current || !isActiveResearchJob(activeJob.current)) {
      cancelRequested.current = false
      setCancelling(false)
      return
    }
    const requestController = controller.current
    const jobId = activeJob.current.id
    stopTimer()
    inflight.current = true
    setBusy(true)
    setError("")
    try { await acceptJob(await cancelStockResearchJob(jobId, token, requestController?.signal), requestController) }
    catch (failure) {
      if (currentRequest(requestController) && (failure as { status?: number })?.status === 409) {
        try {
          const actual = await getStockResearchJob(jobId, token, requestController?.signal)
          if (currentRequest(requestController)) {
            setError("")
            await acceptJob(actual, requestController)
          }
        } catch (refreshFailure) {
          const message = `${failure instanceof Error ? failure.message : "取消未完成"}；刷新任务状态失败：${refreshFailure instanceof Error ? refreshFailure.message : "请求失败"}。请手动重试查询。`
          fail(Object.assign(new Error(message), { status: (refreshFailure as { status?: number })?.status }), requestController)
        }
      } else fail(failure, requestController)
    }
    finally {
      if (currentRequest(requestController)) {
        inflight.current = false
        cancelRequested.current = false
        setCancelling(false)
      }
    }
  }
  const refreshJob = async (id: string) => {
    if (!current() || inflight.current) return
    const requestController = controller.current
    inflight.current = true
    try { await acceptJob(await getStockResearchJob(id, token, requestController?.signal), requestController) }
    catch (failure) { fail(failure, requestController) }
    finally {
      if (currentRequest(requestController)) {
        inflight.current = false
        if (cancelRequested.current) await sendCancel()
      }
    }
  }
  const create = async (draft: ResearchCreateInput) => {
    if (!current() || inflight.current) return
    const requestController = controller.current
    inflight.current = true
    setError("")
    setBusy(true)
    setBlocked(true)
    try { await acceptJob(await createStockResearchJob(draft, token, requestController?.signal), requestController) }
    catch (failure) {
      if (currentRequest(requestController) && (failure as { status?: number }).status === 422 && recovery.current.draft && !recovery.current.jobId) {
        persist({})
        setBlocked(false)
        recoveryReady.current = true
        setRecoveryPending(false)
      }
      fail(failure, requestController)
    }
    finally {
      if (currentRequest(requestController)) {
        inflight.current = false
        if (cancelRequested.current) await sendCancel()
      }
    }
  }
  const refreshAvailability = async () => {
    if (!current() || availabilityInFlight.current) return null
    const requestController = controller.current
    availabilityInFlight.current = true
    setRefreshingAvailability(true)
    try {
      const available = await getStockResearchAvailability(token, requestController?.signal)
      if (!currentRequest(requestController)) return null
      setAvailability(available)
      return available
    } catch (failure) {
      if (currentRequest(requestController)) setError(failure instanceof Error ? failure.message : "服务状态加载失败")
      return null
    } finally {
      if (currentRequest(requestController)) { availabilityInFlight.current = false; setRefreshingAvailability(false) }
    }
  }
  const initialize = async () => {
    if (!current() || inflight.current) return
    const requestController = controller.current
    setError("")
    if (recoveryReadFailed.current) {
      setBlocked(true)
      setError("任务恢复记录无法确认，请在历史报告中核对已有任务，当前禁止替换研究配置。")
      return
    }
    try {
      const available = await refreshAvailability()
      if (!currentRequest(requestController) || !available) return
      if (!recovery.current.jobId && !recovery.current.draft) {
        recoveryReady.current = true
        setRecoveryPending(false)
      }
      if (!available.enabled) return
      if (recovery.current.jobId) await refreshJob(recovery.current.jobId)
      else if (recovery.current.draft) {
        setBlocked(true)
        setError("上次提交结果未知，请使用原幂等键重试确认任务。")
      }
    } catch (failure) { fail(failure, requestController) }
  }
  useEffect(() => {
    mounted.current = true
    controller.current = new AbortController()
    inflight.current = false
    cancelRequested.current = false
    availabilityInFlight.current = false
    recoveryReady.current = false
    recoveryReadFailed.current = false
    setRecoveryPending(true)
    try {
      const saved = localStorage.getItem(storageKey)
      if (saved) recovery.current = recoverySchema.parse(JSON.parse(saved))
    } catch { recoveryReadFailed.current = true; setError("任务恢复记录无效，请在历史报告中查询已有任务。") }
    void initialize()
    return () => { mounted.current = false; stopTimer(); controller.current?.abort() }
  }, [token, scope, sessionRevision])

  const submit = async (kind: ResearchKind, parameters: Record<string, unknown>) => {
    if (!current() || !recoveryReady.current || inflight.current || isActiveResearchJob(activeJob.current) || recovery.current.draft) return
    if (!availability?.enabled || !availability.accepting) { setError(availability?.reason || "研究任务暂未启用或暂不接收任务"); return }
    try {
      const draft = newResearchCreateSchema.parse({ idempotency_key: crypto.randomUUID(), kind, parameters })
      stopTimer()
      setResult(null)
      setJob(null)
      activeJob.current = null
      pollCount.current = 0
      pollStartedAt.current = Date.now()
      persist({ draft })
      await create(draft)
    } catch (failure) { fail(failure) }
  }
  const retry = async () => {
    if (!current() || inflight.current) return
    setError("")
    stopTimer()
    pollCount.current = 0
    pollStartedAt.current = Date.now()
    if (recovery.current.jobId) await refreshJob(recovery.current.jobId)
    else if (recovery.current.draft) await create(recovery.current.draft)
    else await initialize()
  }
  const cancel = async () => {
    if (!current() || cancelRequested.current || !activeJob.current || !isActiveResearchJob(activeJob.current)) return
    cancelRequested.current = true
    setCancelling(true)
    stopTimer()
    if (!inflight.current) await sendCancel()
  }
  return { availability, job, result, error, storageError, busy, blocked, recoveryPending, cancelling, refreshingAvailability, submit, retry, cancel, refreshAvailability }
}

export type ResearchTaskState = ReturnType<typeof useResearchTask>
