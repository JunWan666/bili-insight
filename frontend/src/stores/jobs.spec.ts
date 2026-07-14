import { setActivePinia, createPinia } from 'pinia'
import { ElNotification } from 'element-plus'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useJobsStore } from './jobs'
import type { Job, JobEvent } from '@/types/api'

const api = vi.hoisted(() => ({
  list: vi.fn(),
  createDownload: vi.fn(),
  createDownloadBatch: vi.fn(),
  createAnalysis: vi.fn(),
  cancel: vi.fn(),
  retry: vi.fn(),
  pause: vi.fn(),
  resume: vi.fn(),
}))

vi.mock('@/api', () => ({ jobApi: api }))

class FakeEventSource {
  static instances: FakeEventSource[] = []
  static autoOpen = true
  readonly listeners = new Map<string, Array<(event: MessageEvent<string>) => void>>()
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  closed = false

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this)
    if (FakeEventSource.autoOpen) queueMicrotask(() => this.open())
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject): void {
    if (typeof listener !== 'function') return
    const callbacks = this.listeners.get(type) ?? []
    callbacks.push(listener as (event: MessageEvent<string>) => void)
    this.listeners.set(type, callbacks)
  }

  close(): void { this.closed = true }

  open(): void { this.onopen?.(new Event('open')) }

  emit(type: string, event: JobEvent): void {
    const message = new MessageEvent<string>(type, { data: JSON.stringify(event) })
    for (const listener of this.listeners.get(type) ?? []) listener(message)
  }
}

function job(status: Job['status'] = 'running', id = 'job-1'): Job {
  return {
    id,
    type: 'download',
    status,
    phase: status,
    progress: status === 'completed' ? 100 : 20,
    videoId: 'video-1',
    videoTitle: '视频标题',
    partTitle: '第一 P',
    speedBytesPerSecond: null,
    etaSeconds: null,
    errorCode: null,
    errorMessage: null,
    retryCount: 0,
    cancelRequested: false,
    createdAt: '2026-07-14T00:00:00Z',
    startedAt: '2026-07-14T00:00:01Z',
    finishedAt: null,
    artifactIds: [],
    companionOutcomes: {},
    hasWarnings: false,
  }
}

function event(
  status: JobEvent['status'],
  errorCode: string | null = null,
  errorMessage: string | null = null,
  companionOutcomes: JobEvent['companionOutcomes'] = {},
  hasWarnings = false,
): JobEvent {
  return {
    jobId: 'job-1',
    status,
    phase: status,
    progress: status === 'completed' ? 100 : 20,
    speedBytesPerSecond: null,
    etaSeconds: null,
    errorCode,
    errorMessage,
    companionOutcomes,
    hasWarnings,
    occurredAt: '2026-07-14T00:01:00Z',
  }
}

describe('jobs terminal notifications', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    FakeEventSource.instances = []
    FakeEventSource.autoOpen = true
    vi.stubGlobal('EventSource', FakeEventSource)
    api.list.mockReset()
    api.createDownload.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('notifies a completed SSE transition once and not historical terminal rows', async () => {
    const success = vi.spyOn(ElNotification, 'success').mockImplementation(() => undefined as never)
    api.list
      .mockResolvedValueOnce({ items: [job('running')], total: 1 })
      .mockResolvedValueOnce({ items: [job('running')], total: 1 })
      .mockResolvedValue({ items: [job('completed')], total: 1 })
    const store = useJobsStore()
    await store.refresh()
    const source = FakeEventSource.instances[0]
    expect(source).toBeDefined()
    source!.emit('state', event('completed'))
    source!.emit('state', event('completed'))
    await Promise.resolve()
    expect(success).toHaveBeenCalledTimes(1)

    setActivePinia(createPinia())
    api.list.mockResolvedValue({ items: [job('completed')], total: 1 })
    await useJobsStore().refresh()
    expect(success).toHaveBeenCalledTimes(1)
  })

  it.each([
    ['COOKIE_EXPIRED', '登录凭据已失效，请重新校验', '登录态已失效'],
    ['STORAGE_WRITE_FAILED', '磁盘空间不足，任务已停止', '存储空间不足'],
  ])('uses an actionable title for %s and only the safe error message', async (code, message, title) => {
    const failure = vi.spyOn(ElNotification, 'error').mockImplementation(() => undefined as never)
    api.list
      .mockResolvedValueOnce({ items: [job('running')], total: 1 })
      .mockResolvedValueOnce({ items: [job('running')], total: 1 })
      .mockResolvedValue({ items: [job('failed')], total: 1 })
    const store = useJobsStore()
    await store.refresh()
    FakeEventSource.instances[0]!.emit('state', event('failed', code, message))
    expect(failure).toHaveBeenCalledWith({ title, message })
  })

  it.each([
    [
      { subtitle: 'failed', cover: 'not_available' } as const,
      '任务完成，部分随附内容失败',
      '公开字幕未能保存；封面由视频源标记为不可用。主媒体和其他有效产物已保留。',
    ],
    [
      { subtitle: 'not_available' } as const,
      '任务完成，部分随附内容未提供',
      '公开字幕由视频源标记为不可用；主媒体任务已正常完成。',
    ],
  ])('distinguishes failed and unavailable companion outcomes in SSE completion notifications', async (outcomes, title, message) => {
    const warning = vi.spyOn(ElNotification, 'warning').mockImplementation(() => undefined as never)
    const success = vi.spyOn(ElNotification, 'success').mockImplementation(() => undefined as never)
    api.list
      .mockResolvedValueOnce({ items: [job('running')], total: 1 })
      .mockResolvedValueOnce({ items: [job('running')], total: 1 })
      .mockResolvedValue({ items: [job('completed')], total: 1 })
    const store = useJobsStore()
    await store.refresh()

    FakeEventSource.instances[0]!.emit('state', event('completed', null, null, outcomes, true))

    expect(warning).toHaveBeenCalledWith({ title, message })
    expect(success).not.toHaveBeenCalled()
  })

  it('keeps a server page ordered, updates totals on page two, and never inserts a reused historical job', async () => {
    const pageRow = job('completed', 'page-row')
    api.list.mockImplementation((filters: { activeOnly?: boolean }) => Promise.resolve(
      filters.activeOnly
        ? { items: [], total: 0, page: 1, pageSize: 200 }
        : { items: [pageRow], total: 41, page: 2, pageSize: 20 },
    ))
    const store = useJobsStore()
    await store.refresh({ page: 2, pageSize: 20, type: 'download', status: 'completed' })

    api.createDownload.mockResolvedValueOnce({ job: job('completed', 'job-new'), reused: false })
    await store.createDownload({} as never)
    expect(store.total).toBe(42)
    expect(store.items.map((item) => item.id)).toEqual(['page-row'])

    api.createDownload.mockResolvedValueOnce({ job: job('completed', 'job-reused'), reused: true })
    await store.createDownload({} as never)
    expect(store.total).toBe(42)
    expect(store.items.map((item) => item.id)).toEqual(['page-row'])
    expect(api.list).toHaveBeenCalledWith(expect.objectContaining({
      page: 2,
      pageSize: 20,
      type: 'download',
      status: 'completed',
    }))
  })

  it('loads every active page for SSE without replacing the visible server page', async () => {
    const firstActivePage = Array.from({ length: 200 }, (_, index) => job('running', `active-${index + 1}`))
    const lastActivePage = [job('paused', 'active-201')]
    api.list.mockImplementation((filters: { activeOnly?: boolean; page?: number }) => {
      if (!filters.activeOnly) {
        return Promise.resolve({ items: [job('completed', 'visible-row')], total: 501, page: 3, pageSize: 10 })
      }
      return Promise.resolve(filters.page === 1
        ? { items: firstActivePage, total: 201, page: 1, pageSize: 200 }
        : { items: lastActivePage, total: 201, page: 2, pageSize: 200 })
    })
    const store = useJobsStore()

    await store.refresh({ page: 3, pageSize: 10, status: 'completed' })

    expect(store.items.map((item) => item.id)).toEqual(['visible-row'])
    expect(store.total).toBe(501)
    expect(store.activeJobs).toHaveLength(201)
    expect(store.activeCount).toBe(201)
    expect(api.list).toHaveBeenCalledWith({ activeOnly: true, page: 1, pageSize: 200 })
    expect(api.list).toHaveBeenCalledWith({ activeOnly: true, page: 2, pageSize: 200 })
  })

  it('falls back to polling when an active SSE connection fails', async () => {
    api.list.mockResolvedValue({ items: [job('running')], total: 1, page: 1, pageSize: 20 })
    const store = useJobsStore()
    await store.refresh()
    const source = FakeEventSource.instances[0]!
    const callsBeforeFailure = api.list.mock.calls.length
    vi.useFakeTimers()
    try {
      source.onerror?.(new Event('error'))
      expect(source.closed).toBe(true)
      await vi.advanceTimersByTimeAsync(3000)
      expect(api.list.mock.calls.length).toBeGreaterThan(callsBeforeFailure)
    } finally {
      store.dispose()
      vi.useRealTimers()
    }
  })

  it('keeps polling while any active SSE connection remains unopened and stops after all open', async () => {
    vi.useFakeTimers()
    FakeEventSource.autoOpen = false
    const active = [job('running', 'pending-1'), job('running', 'pending-2')]
    api.list.mockImplementation((filters: { activeOnly?: boolean }) => Promise.resolve(
      filters.activeOnly
        ? { items: active, total: 2, page: 1, pageSize: 200 }
        : { items: active, total: 2, page: 1, pageSize: 20 },
    ))
    const store = useJobsStore()
    try {
      await store.refresh()
      const callsBeforePolling = api.list.mock.calls.length
      FakeEventSource.instances[0]!.open()
      await vi.advanceTimersByTimeAsync(3000)
      expect(api.list.mock.calls.length).toBeGreaterThan(callsBeforePolling)

      FakeEventSource.instances[1]!.open()
      const callsAfterAllOpened = api.list.mock.calls.length
      await vi.advanceTimersByTimeAsync(3000)
      expect(store.connected).toBe(true)
      expect(api.list.mock.calls.length).toBe(callsAfterAllOpened)
    } finally {
      store.dispose()
      vi.useRealTimers()
    }
  })
})
