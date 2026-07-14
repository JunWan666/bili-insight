import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import VideoPreviewDialog from './VideoPreviewDialog.vue'
import type { MediaStream, PreviewSession, VideoDetail, VideoPart } from '@/types/api'

const apiMocks = vi.hoisted(() => ({
  create: vi.fn(),
  remove: vi.fn(),
}))

const playerMocks = vi.hoisted(() => ({
  attach: vi.fn(),
  configure: vi.fn(),
  load: vi.fn(),
  destroy: vi.fn(),
  addEventListener: vi.fn(),
}))

vi.mock('@/api', () => ({
  previewApi: apiMocks,
}))

vi.mock('shaka-player', () => {
  class Player {
    static isBrowserSupported(): boolean { return true }
    attach = playerMocks.attach
    configure = playerMocks.configure
    load = playerMocks.load
    destroy = playerMocks.destroy
    addEventListener = playerMocks.addEventListener
  }
  return { default: { Player, polyfill: { installAll: vi.fn() } } }
})

const video: VideoDetail = {
  id: 'video-1', provider: 'bilibili', bvid: 'BV1TEST', aid: '1', title: '预览测试', description: '',
  coverUrl: 'https://i.example/cover.jpg', ownerName: '测试 UP', duration: 120, publishedAt: null,
  parsedAt: '2026-07-14T00:00:00Z', fromCache: false, accessModeUsed: 'authenticated', authAvailable: true,
  normalizedUrl: 'https://www.bilibili.com/video/BV1TEST', selectedPartId: 'part-1', tags: [],
  statistics: { views: null, likes: null, favorites: null, danmaku: null, coins: null, shares: null },
  rights: { copyright: null, isPaid: false, isPremiumOnly: false }, parts: [],
}

const part: VideoPart = { id: 'part-1', videoId: 'video-1', cid: '100', pageNumber: 1, title: '第一集', duration: 120 }

const videoStream: MediaStream = {
  id: 'stream-video', partId: part.id, kind: 'video', qualityCode: '80', qualityLabel: '1080P',
  codec: 'H.264/AVC', container: 'mp4', width: 1920, height: 1080, fps: 25, bitrate: 3_000_000,
  hdrType: null, audioChannels: null, sampleRate: null, estimatedSize: 45_000_000, authRequired: true,
  premiumRequired: false, accessRequirement: 'login', verifiedAt: null, compatibleDevices: [],
  compatibilityNote: '广泛兼容', mimeType: 'video/mp4', codecString: 'avc1.640028', previewSupported: true,
}

const audioStream: MediaStream = {
  id: 'stream-audio', partId: part.id, kind: 'audio', qualityCode: '30280', qualityLabel: '高码率',
  codec: 'AAC', container: 'mp4', width: null, height: null, fps: null, bitrate: 192_000,
  hdrType: null, audioChannels: 2, sampleRate: 48000, estimatedSize: 2_880_000, authRequired: false,
  premiumRequired: false, accessRequirement: 'none', verifiedAt: null, compatibleDevices: [],
  compatibilityNote: '广泛兼容', mimeType: 'audio/mp4', codecString: 'mp4a.40.2', previewSupported: true,
}

const previewSession: PreviewSession = {
  id: 'preview-1',
  manifestUrl: '/api/v1/previews/preview-1/manifest.mpd',
  expiresAt: '2026-07-14T00:30:00Z',
  duration: 120,
  video: { streamId: videoStream.id, mimeType: 'video/mp4', codecString: 'avc1.640028' },
  audio: { streamId: audioStream.id, mimeType: 'audio/mp4', codecString: 'mp4a.40.2' },
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve
  })
  return { promise, resolve }
}

describe('VideoPreviewDialog', () => {
  beforeEach(() => {
    apiMocks.create.mockResolvedValue(previewSession)
    apiMocks.remove.mockResolvedValue(undefined)
    playerMocks.attach.mockResolvedValue(undefined)
    playerMocks.load.mockResolvedValue(undefined)
    playerMocks.destroy.mockResolvedValue(undefined)
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined)
    Object.defineProperty(window, 'MediaSource', {
      configurable: true,
      value: { isTypeSupported: vi.fn(() => true) },
    })
  })

  afterEach(() => {
    document.body.innerHTML = ''
    vi.restoreAllMocks()
    vi.clearAllMocks()
  })

  it('creates a private preview session and loads its internal manifest', async () => {
    const wrapper = mount(VideoPreviewDialog, {
      attachTo: document.body,
      props: { modelValue: true, video, part, videoStream, audioStream, accessMode: 'authenticated' },
    })
    await flushPromises()

    expect(apiMocks.create).toHaveBeenCalledWith({
      videoStreamId: videoStream.id,
      audioStreamId: audioStream.id,
      accessMode: 'authenticated',
    })
    expect(playerMocks.attach).toHaveBeenCalled()
    expect(playerMocks.load).toHaveBeenCalledWith(previewSession.manifestUrl)
    expect(document.body.textContent).toContain('当前浏览器报告支持所选视频和音频编码')

    await wrapper.setProps({ modelValue: false })
    await flushPromises()
    expect(playerMocks.destroy).toHaveBeenCalled()
    expect(apiMocks.remove).toHaveBeenCalledWith(previewSession.id)
  })

  it('does not create a session when the selected stream lacks preview metadata', async () => {
    mount(VideoPreviewDialog, {
      attachTo: document.body,
      props: {
        modelValue: true,
        video,
        part,
        videoStream: { ...videoStream, previewSupported: false },
        audioStream,
        accessMode: 'authenticated',
      },
    })
    await flushPromises()

    expect(apiMocks.create).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('缺少浏览器预览所需的索引信息')
  })

  it('omits an audio track that lacks preview metadata and explains the video-only fallback', async () => {
    apiMocks.create.mockResolvedValueOnce({ ...previewSession, audio: null })
    const wrapper = mount(VideoPreviewDialog, {
      attachTo: document.body,
      props: {
        modelValue: true,
        video,
        part,
        videoStream,
        audioStream: { ...audioStream, mimeType: null, codecString: null, previewSupported: false },
        accessMode: 'authenticated',
      },
    })
    await flushPromises()

    expect(apiMocks.create).toHaveBeenCalledWith({
      videoStreamId: videoStream.id,
      audioStreamId: null,
      accessMode: 'authenticated',
    })
    expect(document.body.textContent).toContain('所选音轨缺少预览元数据')
    expect(document.body.textContent).toContain('本次将仅播放视频')

    wrapper.unmount()
    await flushPromises()
  })

  it('omits an audio codec that the browser explicitly reports as unsupported', async () => {
    const unsupportedAudio = {
      ...audioStream,
      id: 'stream-audio-dolby',
      codec: 'Dolby E-AC-3',
      codecString: 'ec-3',
    }
    Object.defineProperty(window, 'MediaSource', {
      configurable: true,
      value: { isTypeSupported: vi.fn((value: string) => !value.includes('ec-3')) },
    })
    apiMocks.create.mockResolvedValueOnce({ ...previewSession, audio: null })
    const wrapper = mount(VideoPreviewDialog, {
      attachTo: document.body,
      props: {
        modelValue: true,
        video,
        part,
        videoStream,
        audioStream: unsupportedAudio,
        accessMode: 'authenticated',
      },
    })
    await flushPromises()

    expect(apiMocks.create).toHaveBeenCalledWith({
      videoStreamId: videoStream.id,
      audioStreamId: null,
      accessMode: 'authenticated',
    })
    expect(document.body.textContent).toContain('不支持所选 Dolby E-AC-3 音轨')
    expect(document.body.textContent).toContain('本次将仅播放视频')

    wrapper.unmount()
    await flushPromises()
  })

  it('does not create a session for a video codec the browser cannot decode', async () => {
    Object.defineProperty(window, 'MediaSource', {
      configurable: true,
      value: { isTypeSupported: vi.fn((value: string) => !value.startsWith('video/')) },
    })
    mount(VideoPreviewDialog, {
      attachTo: document.body,
      props: { modelValue: true, video, part, videoStream, audioStream, accessMode: 'authenticated' },
    })
    await flushPromises()

    expect(apiMocks.create).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('当前浏览器不支持所选视频编码')
    expect(document.body.textContent).toContain('改选 H.264 视频规格')
  })

  it('does not resume playback after the dialog closes while the manifest is loading', async () => {
    const loadGate = deferred<void>()
    playerMocks.load.mockImplementationOnce(() => loadGate.promise)
    const wrapper = mount(VideoPreviewDialog, {
      attachTo: document.body,
      props: { modelValue: true, video, part, videoStream, audioStream, accessMode: 'authenticated' },
    })
    await vi.waitFor(() => expect(playerMocks.load).toHaveBeenCalledTimes(1))

    await wrapper.setProps({ modelValue: false })
    await flushPromises()
    expect(playerMocks.destroy).toHaveBeenCalledTimes(1)
    expect(apiMocks.remove).toHaveBeenCalledWith(previewSession.id)

    loadGate.resolve(undefined)
    await flushPromises()
    expect(HTMLMediaElement.prototype.play).not.toHaveBeenCalled()
  })

  it('waits for old player cleanup before creating a session for a new selection', async () => {
    const destroyGate = deferred<void>()
    const nextAudio = { ...audioStream, id: 'stream-audio-next', bitrate: 128_000 }
    const nextSession: PreviewSession = {
      ...previewSession,
      id: 'preview-2',
      audio: { ...previewSession.audio!, streamId: nextAudio.id },
    }
    apiMocks.create
      .mockResolvedValueOnce(previewSession)
      .mockResolvedValueOnce(nextSession)
    playerMocks.destroy.mockImplementationOnce(() => destroyGate.promise)
    const wrapper = mount(VideoPreviewDialog, {
      attachTo: document.body,
      props: { modelValue: true, video, part, videoStream, audioStream, accessMode: 'authenticated' },
    })
    await flushPromises()
    expect(apiMocks.create).toHaveBeenCalledTimes(1)

    await wrapper.setProps({ audioStream: nextAudio })
    await flushPromises()
    expect(playerMocks.destroy).toHaveBeenCalledTimes(1)
    expect(apiMocks.create).toHaveBeenCalledTimes(1)

    destroyGate.resolve(undefined)
    await vi.waitFor(() => expect(apiMocks.create).toHaveBeenCalledTimes(2))
    expect(apiMocks.create).toHaveBeenLastCalledWith({
      videoStreamId: videoStream.id,
      audioStreamId: nextAudio.id,
      accessMode: 'authenticated',
    })
    expect(apiMocks.remove).toHaveBeenCalledWith(previewSession.id)

    wrapper.unmount()
    await flushPromises()
  })
})
