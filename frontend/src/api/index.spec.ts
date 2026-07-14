import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.hoisted(() => vi.fn())
const post = vi.hoisted(() => vi.fn())

vi.mock('./http', () => ({
  http: { get, post, put: vi.fn(), delete: vi.fn() },
  unwrap: (value: unknown) => value,
}))

import { artifactApi, videoApi } from './index'

describe('videoApi stream identity contract', () => {
  beforeEach(() => {
    get.mockReset()
    post.mockReset()
  })

  it('passes authenticated as a legacy response fallback without losing the request parameter', async () => {
    get.mockResolvedValue({
      data: { partId: 'part-1', video: [], audio: [], fetchedAt: '2026-07-14T00:00:00Z' },
    })

    const result = await videoApi.getStreams('video-1', 'part-1', 'authenticated')

    expect(get).toHaveBeenCalledWith('/videos/video-1/parts/part-1/streams', {
      params: { accessMode: 'authenticated' },
    })
    expect(result).toMatchObject({ partId: 'part-1', accessModeUsed: 'authenticated', authAvailable: true })
  })

  it('honors an explicit backend downgrade over the requested fallback identity', async () => {
    get.mockResolvedValue({
      data: {
        partId: 'part-1',
        video: [],
        audio: [],
        access: { actualMode: 'anonymous', hasCredentials: false, usedAuthentication: false },
      },
    })

    const result = await videoApi.getStreams('video-1', 'part-1', 'authenticated')

    expect(result).toMatchObject({ accessModeUsed: 'anonymous', authAvailable: false })
  })

  it('verifies a stream without exposing its temporary media URL', async () => {
    post.mockResolvedValue({
      data: { streamId: 'stream/with space', verifiedAt: '2026-07-14T08:00:00Z' },
    })

    await expect(videoApi.verifyStream('stream/with space', 'anonymous')).resolves.toEqual({
      streamId: 'stream/with space',
      verifiedAt: '2026-07-14T08:00:00Z',
    })
    expect(post).toHaveBeenCalledWith('/videos/streams/stream%2Fwith%20space/verify', {
      accessMode: 'anonymous',
    })
  })
})

describe('artifactApi storage contract', () => {
  beforeEach(() => get.mockReset())

  it('loads storage independently from the optional diagnostics endpoint', async () => {
    get.mockResolvedValue({ data: { artifactBytes: 10, freeBytes: 20, totalBytes: 40 } })

    await expect(artifactApi.storage()).resolves.toEqual({ artifactBytes: 10, freeBytes: 20, totalBytes: 40 })
    expect(get).toHaveBeenCalledWith('/artifacts/storage')
  })
})
