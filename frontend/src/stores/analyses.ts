import { ref } from 'vue'
import { defineStore } from 'pinia'
import { analysisApi } from '@/api'
import { toApiError, type ApiError } from '@/api/errors'
import type { AnalysisCapability, AnalysisRecord } from '@/types/api'

export const useAnalysesStore = defineStore('analyses', () => {
  const items = ref<AnalysisRecord[]>([])
  const total = ref(0)
  const loading = ref(false)
  const error = ref<ApiError | null>(null)
  const capabilities = ref<AnalysisCapability[]>([])
  const capabilitiesLoading = ref(false)
  const capabilitiesError = ref<ApiError | null>(null)
  const scopeKey = ref<string | null>(null)
  const loaded = ref(false)
  const refreshedAt = ref<string | null>(null)
  let requestVersion = 0

  async function loadCapabilities(force = false): Promise<void> {
    if (capabilitiesLoading.value || (!force && capabilities.value.length > 0)) return
    capabilitiesLoading.value = true
    capabilitiesError.value = null
    try {
      capabilities.value = await analysisApi.capabilities()
    } catch (reason) {
      capabilitiesError.value = toApiError(reason)
    } finally {
      capabilitiesLoading.value = false
    }
  }

  async function load(videoId: string, partId: string, force = false): Promise<void> {
    const nextScope = `${videoId}:${partId}`
    if (!force && scopeKey.value === nextScope && loaded.value) {
      void loadCapabilities()
      return
    }

    if (scopeKey.value !== nextScope) {
      scopeKey.value = nextScope
      items.value = []
      total.value = 0
      loaded.value = false
      refreshedAt.value = null
    }

    const version = ++requestVersion
    loading.value = true
    error.value = null
    void loadCapabilities()
    try {
      const response = await analysisApi.list({ videoId, partId, limit: 200, offset: 0 })
      if (version !== requestVersion || scopeKey.value !== nextScope) return
      items.value = response.items
      total.value = response.total
      loaded.value = true
      refreshedAt.value = new Date().toISOString()
    } catch (reason) {
      if (version === requestVersion && scopeKey.value === nextScope) {
        error.value = toApiError(reason)
      }
    } finally {
      if (version === requestVersion) loading.value = false
    }
  }

  function refresh(videoId: string, partId: string): Promise<void> {
    return load(videoId, partId, true)
  }

  return {
    items,
    total,
    loading,
    error,
    capabilities,
    capabilitiesLoading,
    capabilitiesError,
    scopeKey,
    loaded,
    refreshedAt,
    load,
    refresh,
    loadCapabilities,
  }
})
