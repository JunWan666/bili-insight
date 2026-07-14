import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import BatchDownloadDrawer from './BatchDownloadDrawer.vue'
import type { Job, MediaStream, StreamCollection, VideoDetail, VideoPart } from '@/types/api'

const getStreams = vi.hoisted(() => vi.fn())
const createDownloadBatch = vi.hoisted(() => vi.fn())

vi.mock('@/api', () => ({ videoApi: { getStreams } }))
vi.mock('@/stores/jobs', () => ({ useJobsStore: () => ({ createDownloadBatch }) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

const parts: VideoPart[] = [1, 2, 3].map((pageNumber) => ({
  id: `part-${pageNumber}`,
  videoId: 'video-1',
  cid: String(pageNumber),
  pageNumber,
  title: `第 ${pageNumber} P`,
  duration: 30,
}))
const video = {
  id: 'video-1',
  bvid: 'BV1TEST',
  title: '批量测试视频',
  parts,
} as VideoDetail

function stream(partId: string, kind: 'video' | 'audio', codec: string, height: number | null): MediaStream {
  return {
    id: `${partId}-${kind}-${codec}`,
    partId,
    kind,
    qualityCode: height ? String(height) : '30280',
    qualityLabel: height ? `${height}P` : '高码率',
    codec,
    container: 'm4s',
    width: height ? Math.round(height * 16 / 9) : null,
    height,
    fps: height ? 25 : null,
    bitrate: height ? height * 2_000 : 192_000,
    hdrType: null,
    audioChannels: kind === 'audio' ? 2 : null,
    sampleRate: kind === 'audio' ? 48_000 : null,
    estimatedSize: height ? height * 10_000 : 500_000,
    authRequired: false,
    premiumRequired: false,
    accessRequirement: 'none',
    verifiedAt: null,
    compatibleDevices: [],
    compatibilityNote: null,
  }
}

function streamsFor(partId: string, accessMode: 'anonymous' | 'authenticated' = 'anonymous'): StreamCollection {
  return {
    partId,
    accessModeUsed: accessMode,
    authAvailable: accessMode === 'authenticated',
    parsedAt: '2026-07-14T00:00:00Z',
    videos: [stream(partId, 'video', 'H.264/AVC', 720)],
    audios: [stream(partId, 'audio', 'AAC', null)],
  }
}

function job(id: string, status: Job['status']): Job {
  return {
    id,
    type: 'download',
    status,
    phase: status,
    progress: status === 'completed' ? 100 : 0,
    videoId: 'video-1',
    videoTitle: '批量测试视频',
    partTitle: null,
    speedBytesPerSecond: null,
    etaSeconds: null,
    errorCode: null,
    errorMessage: null,
    retryCount: 0,
    cancelRequested: false,
    createdAt: '2026-07-14T00:00:00Z',
    startedAt: null,
    finishedAt: status === 'completed' ? '2026-07-14T00:01:00Z' : null,
    artifactIds: [],
    companionOutcomes: {},
    hasWarnings: false,
  }
}

function mountDrawer(extraProps: Record<string, unknown> = {}) {
  return mount(BatchDownloadDrawer, {
    props: {
      modelValue: true,
      video,
      initialPartId: 'part-2',
      accessMode: 'anonymous',
      ...extraProps,
    },
    global: {
      stubs: {
        ElDrawer: { template: '<div><slot name="header"/><slot/><slot name="footer"/></div>' },
      },
    },
  })
}

describe('BatchDownloadDrawer', () => {
  beforeEach(() => {
    getStreams.mockReset()
    createDownloadBatch.mockReset()
    getStreams.mockImplementation((_videoId: string, partId: string, accessMode: 'anonymous' | 'authenticated') => (
      Promise.resolve(streamsFor(partId, accessMode))
    ))
    createDownloadBatch.mockResolvedValue({
      items: [
        { job: job('job-part-1', 'queued'), reused: false },
        { job: job('job-part-2', 'completed'), reused: true },
        { job: job('job-part-3', 'queued'), reused: false },
      ],
      createdCount: 2,
      reusedCount: 1,
    })
  })

  it('resolves every part with one shared snapshot and preserves ordered reuse feedback', async () => {
    let resolveFirst: ((value: StreamCollection) => void) | null = null
    getStreams
      .mockImplementationOnce(() => new Promise<StreamCollection>((resolve) => { resolveFirst = resolve }))
      .mockImplementation((_videoId: string, partId: string, accessMode: 'anonymous' | 'authenticated') => (
        Promise.resolve(streamsFor(partId, accessMode))
      ))
    const wrapper = mountDrawer()

    await wrapper.get('[data-testid="create-download-batch"]').trigger('click')
    await flushPromises()
    expect(getStreams).toHaveBeenCalledTimes(1)
    expect(wrapper.findAll('.preset-grid button').every((button) => button.attributes('disabled') !== undefined)).toBe(true)
    expect(wrapper.findAll('.part-list input[type="checkbox"]').every((input) => input.attributes('disabled') !== undefined)).toBe(true)
    expect(wrapper.get('input[maxlength="180"]').attributes('disabled')).toBeDefined()

    await wrapper.setProps({ accessMode: 'authenticated' })
    resolveFirst?.(streamsFor('part-1', 'anonymous'))
    await flushPromises()

    expect(getStreams.mock.calls.map((call) => [call[1], call[2]])).toEqual([
      ['part-1', 'anonymous'],
      ['part-2', 'anonymous'],
      ['part-3', 'anonymous'],
    ])
    expect(createDownloadBatch).toHaveBeenCalledWith({
      downloads: expect.arrayContaining([
        expect.objectContaining({ partId: 'part-1', accessMode: 'anonymous', includeDanmaku: false }),
        expect.objectContaining({ partId: 'part-2', accessMode: 'anonymous', includeDanmaku: false }),
        expect.objectContaining({ partId: 'part-3', accessMode: 'anonymous', includeDanmaku: false }),
      ]),
    })
    expect(createDownloadBatch.mock.calls[0]?.[0].downloads.map((request: { partId: string }) => request.partId)).toEqual([
      'part-1', 'part-2', 'part-3',
    ])
    expect(wrapper.findAll('.resolution-section article').map((row) => row.text())).toEqual([
      expect.stringContaining('P1第 1 P'),
      expect.stringContaining('P2第 2 P'),
      expect.stringContaining('P3第 3 P'),
    ])
    expect(wrapper.text()).toContain('已复用')
    expect(wrapper.emitted('created')).toEqual([[['job-part-1', 'job-part-2', 'job-part-3']]])
  })

  it('shows the minimum-resolution fallback instead of silently choosing a lower stream', async () => {
    const wrapper = mountDrawer({ preset: 'smallest', minimumResolutionHeight: 1080 })
    await wrapper.get('[data-testid="create-download-batch"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('没有达到 1080P 的流，已回退到现有规格')
    expect(createDownloadBatch).toHaveBeenCalledTimes(1)
  })

  it('keeps authenticated streams and download requests in the authenticated context', async () => {
    const wrapper = mountDrawer({ accessMode: 'authenticated' })
    await wrapper.get('[data-testid="create-download-batch"]').trigger('click')
    await flushPromises()

    expect(getStreams.mock.calls.every((call) => call[2] === 'authenticated')).toBe(true)
    expect(createDownloadBatch.mock.calls[0]?.[0].downloads.every((request: { accessMode: string }) => (
      request.accessMode === 'authenticated'
    ))).toBe(true)
  })
})
