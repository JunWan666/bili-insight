import { ref } from 'vue'
import { defineStore } from 'pinia'
import { videoApi } from '@/api'
import { toApiError, type ApiError } from '@/api/errors'
import type {
  AccessMode,
  RecentVideo,
  StreamCollection,
  StreamVerificationResult,
  VideoBatchDeleteResult,
  VideoDetail,
} from '@/types/api'

export const useVideosStore = defineStore('videos', () => {
  const current = ref<VideoDetail | null>(null)
  const streams = ref<StreamCollection | null>(null)
  const recent = ref<RecentVideo[]>([])
  const loading = ref(false)
  const streamsLoading = ref(false)
  const error = ref<ApiError | null>(null)

  async function parse(url: string, accessMode: AccessMode, forceRefresh = false): Promise<VideoDetail> {
    loading.value = true
    error.value = null
    try {
      const result = await videoApi.parse(url, accessMode, forceRefresh)
      current.value = result.video
      streams.value = result.streams
      return result.video
    } catch (reason) {
      const normalized = toApiError(reason)
      error.value = normalized
      throw normalized
    } finally {
      loading.value = false
    }
  }

  async function load(videoId: string): Promise<VideoDetail> {
    if (current.value?.id === videoId) return current.value
    loading.value = true
    error.value = null
    try {
      const result = await videoApi.get(videoId)
      current.value = result
      return result
    } catch (reason) {
      const normalized = toApiError(reason)
      error.value = normalized
      throw normalized
    } finally {
      loading.value = false
    }
  }

  async function loadStreams(videoId: string, partId: string, accessMode?: 'anonymous' | 'authenticated'): Promise<StreamCollection> {
    streamsLoading.value = true
    error.value = null
    try {
      const result = await videoApi.getStreams(videoId, partId, accessMode)
      streams.value = result
      return result
    } catch (reason) {
      const normalized = toApiError(reason)
      error.value = normalized
      throw normalized
    } finally {
      streamsLoading.value = false
    }
  }

  async function refresh(accessMode: 'anonymous' | 'authenticated'): Promise<VideoDetail> {
    if (!current.value) throw new Error('没有可刷新的视频')
    loading.value = true
    error.value = null
    try {
      const result = await videoApi.refresh(current.value.id, accessMode)
      current.value = result
      streams.value = null
      return result
    } catch (reason) {
      const normalized = toApiError(reason)
      error.value = normalized
      throw normalized
    } finally {
      loading.value = false
    }
  }

  async function verifyStream(
    streamId: string,
    accessMode: 'anonymous' | 'authenticated',
  ): Promise<StreamVerificationResult> {
    error.value = null
    try {
      const result = await videoApi.verifyStream(streamId, accessMode)
      if (streams.value) {
        streams.value = {
          ...streams.value,
          videos: streams.value.videos.map((stream) => (
            stream.id === result.streamId ? { ...stream, verifiedAt: result.verifiedAt } : stream
          )),
          audios: streams.value.audios.map((stream) => (
            stream.id === result.streamId ? { ...stream, verifiedAt: result.verifiedAt } : stream
          )),
        }
      }
      return result
    } catch (reason) {
      const normalized = toApiError(reason)
      error.value = normalized
      throw normalized
    }
  }

  async function loadRecent(limit = 8): Promise<void> {
    try {
      recent.value = await videoApi.recent(limit)
    } catch {
      recent.value = []
    }
  }

  async function removeRecent(videoId: string): Promise<void> {
    await videoApi.remove(videoId)
    recent.value = recent.value.filter((item) => item.id !== videoId)
    if (current.value?.id === videoId) {
      current.value = null
      streams.value = null
    }
  }

  async function removeRecentMany(videoIds: string[]): Promise<VideoBatchDeleteResult> {
    const result = await videoApi.removeMany(videoIds)
    const deletedIds = new Set(result.results.map((item) => item.id))
    recent.value = recent.value.filter((item) => !deletedIds.has(item.id))
    if (current.value && deletedIds.has(current.value.id)) {
      current.value = null
      streams.value = null
    }
    return result
  }

  function clearAuthenticatedContext(): void {
    if (streams.value?.accessModeUsed === 'authenticated') streams.value = null
    else if (streams.value) streams.value = { ...streams.value, authAvailable: false }
    if (current.value) {
      current.value = {
        ...current.value,
        accessModeUsed: 'anonymous',
        authAvailable: false,
      }
    }
  }

  return {
    current,
    streams,
    recent,
    loading,
    streamsLoading,
    error,
    parse,
    load,
    loadStreams,
    verifyStream,
    refresh,
    loadRecent,
    removeRecent,
    removeRecentMany,
    clearAuthenticatedContext,
  }
})
