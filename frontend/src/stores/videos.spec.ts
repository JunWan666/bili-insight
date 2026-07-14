import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useVideosStore } from './videos'
import type { StreamCollection, VideoDetail } from '@/types/api'

const getStreams = vi.hoisted(() => vi.fn())
const verifyStream = vi.hoisted(() => vi.fn())

vi.mock('@/api', () => ({
  videoApi: {
    getStreams,
    verifyStream,
    parse: vi.fn(),
    get: vi.fn(),
    refresh: vi.fn(),
    recent: vi.fn(),
  },
}))

describe('videos stream identity state', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    getStreams.mockReset()
    verifyStream.mockReset()
  })

  it('retains an authenticated stream collection for downstream download creation', async () => {
    const response: StreamCollection = {
      partId: 'part-1',
      accessModeUsed: 'authenticated',
      authAvailable: true,
      parsedAt: '2026-07-14T00:00:00Z',
      videos: [],
      audios: [],
    }
    getStreams.mockResolvedValue(response)
    const store = useVideosStore()

    await store.loadStreams('video-1', 'part-1', 'authenticated')

    expect(getStreams).toHaveBeenCalledWith('video-1', 'part-1', 'authenticated')
    expect(store.streams).toEqual(response)
    expect(store.streams?.accessModeUsed).toBe('authenticated')
  })

  it('drops authenticated streams and identity immediately after credentials are cleared', () => {
    const store = useVideosStore()
    store.current = {
      id: 'video-1',
      accessModeUsed: 'authenticated',
      authAvailable: true,
    } as VideoDetail
    store.streams = {
      partId: 'part-1',
      accessModeUsed: 'authenticated',
      authAvailable: true,
      parsedAt: '2026-07-14T00:00:00Z',
      videos: [],
      audios: [],
    }

    store.clearAuthenticatedContext()

    expect(store.streams).toBeNull()
    expect(store.current).toMatchObject({ accessModeUsed: 'anonymous', authAvailable: false })
  })

  it('retains anonymous streams while removing their saved-credential availability flag', () => {
    const store = useVideosStore()
    store.streams = {
      partId: 'part-1',
      accessModeUsed: 'anonymous',
      authAvailable: true,
      parsedAt: '2026-07-14T00:00:00Z',
      videos: [],
      audios: [],
    }

    store.clearAuthenticatedContext()

    expect(store.streams).toMatchObject({ accessModeUsed: 'anonymous', authAvailable: false })
  })

  it('updates only the verified stream with safe verification evidence', async () => {
    verifyStream.mockResolvedValue({
      streamId: 'video-stream',
      verifiedAt: '2026-07-14T08:00:00Z',
    })
    const store = useVideosStore()
    store.streams = {
      partId: 'part-1',
      accessModeUsed: 'anonymous',
      authAvailable: false,
      parsedAt: '2026-07-14T00:00:00Z',
      videos: [{ id: 'video-stream', verifiedAt: null } as StreamCollection['videos'][number]],
      audios: [{ id: 'audio-stream', verifiedAt: null } as StreamCollection['audios'][number]],
    }

    await store.verifyStream('video-stream', 'anonymous')

    expect(verifyStream).toHaveBeenCalledWith('video-stream', 'anonymous')
    expect(store.streams?.videos[0]?.verifiedAt).toBe('2026-07-14T08:00:00Z')
    expect(store.streams?.audios[0]?.verifiedAt).toBeNull()
  })
})
