import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { ElNotification } from 'element-plus'
import { jobApi } from '@/api'
import { normalizeJobEvent } from '@/api/adapters'
import { toApiError, type ApiError } from '@/api/errors'
import { apiBaseUrl } from '@/api/http'
import { camelize } from '@/utils/case'
import type {
  CreateAnalysisRequest,
  CreateDownloadBatchRequest,
  CreateDownloadRequest,
  DownloadBatchCreationResult,
  DownloadCreationResult,
  Job,
  JobEvent,
  JobBatchDeleteResult,
  JobFilters,
} from '@/types/api'

const terminalStatuses = new Set(['completed', 'canceled', 'failed'])
const defaultFilters: Required<Pick<JobFilters, 'page' | 'pageSize'>> = { page: 1, pageSize: 20 }
const companionLabels = { cover: '封面', subtitle: '公开字幕', danmaku: '弹幕 XML', metadata: '元数据' } as const

export const useJobsStore = defineStore('jobs', () => {
  const items = ref<Job[]>([])
  const activeJobs = ref<Job[]>([])
  const total = ref(0)
  const page = ref(defaultFilters.page)
  const pageSize = ref(defaultFilters.pageSize)
  const loading = ref(false)
  const activeLoading = ref(false)
  const error = ref<ApiError | null>(null)
  const connected = ref(false)
  const sources = new Map<string, EventSource>()
  const openedSources = new Set<string>()
  const terminalNotifications = new Set<string>()
  let currentFilters: JobFilters = { ...defaultFilters }
  let refreshTimer: number | null = null
  let activeRefresh: Promise<void> | null = null

  const activeCount = computed(() => activeJobs.value.length)

  function normalizedFilters(filters: JobFilters): JobFilters {
    return {
      status: filters.status,
      type: filters.type,
      activeOnly: filters.activeOnly === true || undefined,
      page: Math.max(1, filters.page ?? defaultFilters.page),
      pageSize: Math.max(1, Math.min(200, filters.pageSize ?? defaultFilters.pageSize)),
    }
  }

  function updateIn(list: Job[], job: Job): boolean {
    const index = list.findIndex((item) => item.id === job.id)
    if (index === -1) return false
    list[index] = job
    return true
  }

  function matchesCurrent(job: Job): boolean {
    if (currentFilters.type && job.type !== currentFilters.type) return false
    if (currentFilters.status && job.status !== currentFilters.status) return false
    if (currentFilters.activeOnly && terminalStatuses.has(job.status)) return false
    return true
  }

  function track(job: Job, options: { incrementTotal?: boolean } = {}): void {
    const wasOnPage = updateIn(items.value, job)
    if (!wasOnPage && options.incrementTotal && matchesCurrent(job)) total.value += 1

    if (terminalStatuses.has(job.status)) {
      activeJobs.value = activeJobs.value.filter((item) => item.id !== job.id)
      disconnect(job.id)
      return
    }
    if (!updateIn(activeJobs.value, job)) activeJobs.value = [job, ...activeJobs.value]
    connect(job.id)
  }

  function failureTitle(event: JobEvent): string {
    const code = event.errorCode?.toUpperCase() ?? ''
    if (code.includes('COOKIE') || code.includes('AUTHENTICATION')) return '登录态已失效'
    if (
      code.includes('STORAGE')
      || code.includes('DISK')
      || code.includes('QUOTA')
      || /磁盘|空间不足|配额/.test(event.errorMessage ?? '')
    ) return '存储空间不足'
    return '任务执行失败'
  }

  function notifyTerminal(event: JobEvent): void {
    if (event.status !== 'completed' && event.status !== 'failed') return
    const key = `${event.jobId}:${event.status}`
    if (terminalNotifications.has(key)) return
    terminalNotifications.add(key)
    if (event.status === 'completed') {
      if (event.hasWarnings) {
        const failed = Object.entries(event.companionOutcomes)
          .filter(([, outcome]) => outcome === 'failed')
          .map(([type]) => companionLabels[type as keyof typeof companionLabels])
        const unavailable = Object.entries(event.companionOutcomes)
          .filter(([, outcome]) => outcome === 'not_available')
          .map(([type]) => companionLabels[type as keyof typeof companionLabels])
        ElNotification.warning({
          title: failed.length ? '任务完成，部分随附内容失败' : '任务完成，部分随附内容未提供',
          message: failed.length
            ? `${failed.join('、')}未能保存${unavailable.length ? `；${unavailable.join('、')}由视频源标记为不可用` : ''}。主媒体和其他有效产物已保留。`
            : `${unavailable.join('、') || '部分随附内容'}由视频源标记为不可用；主媒体任务已正常完成。`,
        })
        return
      }
      ElNotification.success({ title: '任务已完成', message: '产物已可在任务中心查看。' })
      return
    }
    ElNotification.error({
      title: failureTitle(event),
      message: event.errorMessage || '任务未能完成，请在任务中心查看详情。',
    })
  }

  function applyEventTo(job: Job, event: JobEvent): void {
    Object.assign(job, {
      status: event.status,
      phase: event.phase,
      progress: event.progress,
      speedBytesPerSecond: event.speedBytesPerSecond,
      etaSeconds: event.etaSeconds,
      errorCode: event.errorCode,
      errorMessage: event.errorMessage,
      companionOutcomes: event.companionOutcomes,
      hasWarnings: event.hasWarnings,
    })
  }

  function applyEvent(event: JobEvent): void {
    const current = items.value.find((item) => item.id === event.jobId)
    const active = activeJobs.value.find((item) => item.id === event.jobId)
    const previousStatus = active?.status ?? current?.status
    if (current) applyEventTo(current, event)
    if (active && active !== current) applyEventTo(active, event)
    if (!terminalStatuses.has(event.status)) return

    if (previousStatus && !terminalStatuses.has(previousStatus)) notifyTerminal(event)
    activeJobs.value = activeJobs.value.filter((item) => item.id !== event.jobId)
    disconnect(event.jobId)
    void Promise.all([refresh(), refreshActive()])
  }

  function connect(jobId: string): void {
    if (sources.has(jobId) || typeof EventSource === 'undefined') return
    const source = new EventSource(`${apiBaseUrl()}/jobs/${encodeURIComponent(jobId)}/events`)
    source.onopen = () => {
      openedSources.add(jobId)
      syncConnectionState()
      if (connected.value) stopPolling()
    }
    const handleMessage = (message: MessageEvent<string>): void => {
      try {
        applyEvent(normalizeJobEvent(camelize<unknown>(JSON.parse(message.data) as unknown)))
      } catch {
        disconnect(jobId)
        startPolling()
      }
    }
    source.onmessage = handleMessage
    source.addEventListener('snapshot', handleMessage)
    source.addEventListener('progress', handleMessage)
    source.addEventListener('state', handleMessage)
    source.onerror = () => {
      disconnect(jobId)
      startPolling()
    }
    sources.set(jobId, source)
    syncConnectionState()
  }

  function disconnect(jobId: string): void {
    const source = sources.get(jobId)
    source?.close()
    sources.delete(jobId)
    openedSources.delete(jobId)
    syncConnectionState()
  }

  function syncConnectionState(): void {
    connected.value = activeJobs.value.length > 0
      && openedSources.size === activeJobs.value.length
  }

  function connectActive(): void {
    const activeIds = new Set(activeJobs.value.map((job) => job.id))
    for (const jobId of sources.keys()) {
      if (!activeIds.has(jobId)) disconnect(jobId)
    }
    for (const job of activeJobs.value) connect(job.id)
    syncConnectionState()
    if (activeJobs.value.length > 0 && !connected.value) startPolling()
  }

  function startPolling(): void {
    if (refreshTimer !== null || activeCount.value === 0) return
    refreshTimer = window.setInterval(() => {
      if (document.visibilityState !== 'visible') return
      void Promise.all([refresh(), refreshActive()])
    }, 3000)
  }

  function stopPolling(): void {
    if (refreshTimer !== null) window.clearInterval(refreshTimer)
    refreshTimer = null
  }

  async function refreshActive(): Promise<void> {
    if (activeRefresh) return activeRefresh
    activeRefresh = (async () => {
      activeLoading.value = true
      try {
        const collected: Job[] = []
        const seen = new Set<string>()
        let activePage = 1
        const activePageSize = 200
        let expectedTotal = Number.MAX_SAFE_INTEGER
        while (collected.length < expectedTotal) {
          const result = await jobApi.list({ activeOnly: true, page: activePage, pageSize: activePageSize })
          expectedTotal = result.total
          let added = 0
          for (const job of result.items) {
            if (terminalStatuses.has(job.status) || seen.has(job.id)) continue
            seen.add(job.id)
            collected.push(job)
            added += 1
          }
          if (result.items.length < activePageSize || collected.length >= expectedTotal || added === 0) break
          activePage += 1
        }
        activeJobs.value = collected
        connectActive()
        if (activeJobs.value.length === 0) stopPolling()
      } catch {
        if (activeJobs.value.length > 0) startPolling()
      } finally {
        activeLoading.value = false
        activeRefresh = null
      }
    })()
    return activeRefresh
  }

  async function refresh(filters?: JobFilters): Promise<void> {
    if (filters) currentFilters = normalizedFilters(filters)
    else currentFilters = normalizedFilters(currentFilters)
    loading.value = true
    error.value = null
    try {
      const result = await jobApi.list(currentFilters)
      items.value = result.items
      total.value = result.total
      page.value = result.page
      pageSize.value = result.pageSize
      for (const job of result.items) {
        if (!terminalStatuses.has(job.status) && !activeJobs.value.some((item) => item.id === job.id)) {
          activeJobs.value.push(job)
        }
      }
      connectActive()
      await refreshActive()
    } catch (reason) {
      error.value = toApiError(reason)
      if (activeCount.value > 0) startPolling()
    } finally {
      loading.value = false
    }
  }

  async function createDownload(request: CreateDownloadRequest): Promise<DownloadCreationResult> {
    const result = await jobApi.createDownload(request)
    track(result.job, { incrementTotal: !result.reused })
    return result
  }

  async function createDownloadBatch(request: CreateDownloadBatchRequest): Promise<DownloadBatchCreationResult> {
    const result = await jobApi.createDownloadBatch(request)
    for (const item of result.items) {
      track(item.job, { incrementTotal: !item.reused })
    }
    return result
  }

  async function createAnalysis(request: CreateAnalysisRequest): Promise<Job> {
    const job = await jobApi.createAnalysis(request)
    track(job, { incrementTotal: true })
    return job
  }

  async function cancel(jobId: string): Promise<void> {
    track(await jobApi.cancel(jobId))
  }

  async function retry(jobId: string): Promise<void> {
    const job = await jobApi.retry(jobId)
    terminalNotifications.delete(`${jobId}:completed`)
    terminalNotifications.delete(`${jobId}:failed`)
    track(job)
  }

  async function pause(jobId: string): Promise<void> {
    track(await jobApi.pause(jobId))
  }

  async function resume(jobId: string): Promise<void> {
    track(await jobApi.resume(jobId))
  }

  async function remove(jobId: string): Promise<void> {
    await jobApi.remove(jobId)
    items.value = items.value.filter((item) => item.id !== jobId)
    activeJobs.value = activeJobs.value.filter((item) => item.id !== jobId)
    total.value = Math.max(0, total.value - 1)
    disconnect(jobId)
  }

  async function removeMany(jobIds: string[]): Promise<JobBatchDeleteResult> {
    const result = await jobApi.removeMany(jobIds)
    const deletedIds = new Set(result.results.map((item) => item.id))
    items.value = items.value.filter((item) => !deletedIds.has(item.id))
    activeJobs.value = activeJobs.value.filter((item) => !deletedIds.has(item.id))
    total.value = Math.max(0, total.value - result.deletedCount)
    for (const jobId of deletedIds) disconnect(jobId)
    return result
  }

  function dispose(): void {
    for (const source of sources.values()) source.close()
    sources.clear()
    openedSources.clear()
    terminalNotifications.clear()
    stopPolling()
    connected.value = false
  }

  return {
    items,
    activeJobs,
    total,
    page,
    pageSize,
    loading,
    activeLoading,
    error,
    connected,
    activeCount,
    refresh,
    refreshActive,
    createDownload,
    createDownloadBatch,
    createAnalysis,
    cancel,
    retry,
    pause,
    resume,
    remove,
    removeMany,
    dispose,
  }
})
