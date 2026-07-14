import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { authApi } from '@/api'
import { toApiError, type ApiError } from '@/api/errors'
import type { AuthStatus } from '@/types/api'

export const useAuthStore = defineStore('auth', () => {
  const status = ref<AuthStatus | null>(null)
  const loading = ref(false)
  const error = ref<ApiError | null>(null)
  let pending: Promise<AuthStatus> | null = null

  const isAuthenticated = computed(() => Boolean(status.value?.isAuthenticated))
  const isPremium = computed(() => Boolean(status.value?.isPremium))

  function run(action: () => Promise<AuthStatus>): Promise<AuthStatus> {
    loading.value = true
    error.value = null
    const request = action()
      .then((result) => {
        status.value = result
        return result
      })
      .catch((reason: unknown) => {
        const normalized = toApiError(reason)
        error.value = normalized
        throw normalized
      })
      .finally(() => {
        loading.value = false
        pending = null
      })
    pending = request
    return request
  }

  function load(force = false): Promise<AuthStatus> {
    if (pending && !force) return pending
    return run(authApi.status)
  }

  function upload(file: File, remember: boolean): Promise<AuthStatus> {
    return run(() => authApi.upload(file, remember))
  }

  function validate(): Promise<AuthStatus> {
    return run(authApi.validate)
  }

  async function clear(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      await authApi.clear()
      status.value = {
        status: 'anonymous',
        isAuthenticated: false,
        isPremium: false,
        maskedAccountName: null,
        membershipType: null,
        cookieExpiresAt: null,
        lastValidatedAt: null,
        remembered: false,
        message: null,
      }
    } catch (reason) {
      const normalized = toApiError(reason)
      error.value = normalized
      throw normalized
    } finally {
      loading.value = false
    }
  }

  return { status, loading, error, isAuthenticated, isPremium, load, upload, validate, clear }
})
