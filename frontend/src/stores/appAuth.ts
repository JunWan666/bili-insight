import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { appAuthApi } from '@/api'
import type {
  AppAuthStatus,
  AppLoginRequest,
  AppPasswordChangeRequest,
  AppSetupRequest,
} from '@/types/api'

export const useAppAuthStore = defineStore('app-auth', () => {
  const status = ref<AppAuthStatus | null>(null)
  const loading = ref(false)
  let request: Promise<AppAuthStatus> | null = null

  const initialized = computed(() => status.value?.initialized === true)
  const authenticated = computed(() => status.value?.authenticated === true)

  async function load(force = false): Promise<AppAuthStatus> {
    if (status.value && !force) return status.value
    if (request) return request
    loading.value = true
    request = appAuthApi.status()
    try {
      status.value = await request
      return status.value
    } finally {
      request = null
      loading.value = false
    }
  }

  async function setup(payload: AppSetupRequest): Promise<AppAuthStatus> {
    loading.value = true
    try {
      status.value = await appAuthApi.setup(payload)
      return status.value
    } finally {
      loading.value = false
    }
  }

  async function login(payload: AppLoginRequest): Promise<AppAuthStatus> {
    loading.value = true
    try {
      status.value = await appAuthApi.login(payload)
      return status.value
    } finally {
      loading.value = false
    }
  }

  async function logout(): Promise<void> {
    loading.value = true
    try {
      status.value = await appAuthApi.logout()
    } finally {
      loading.value = false
    }
  }

  async function changePassword(payload: AppPasswordChangeRequest): Promise<void> {
    loading.value = true
    try {
      status.value = await appAuthApi.changePassword(payload)
    } finally {
      loading.value = false
    }
  }

  function expire(): void {
    status.value = status.value
      ? { ...status.value, authenticated: false, csrfToken: null, sessionExpiresAt: null }
      : null
  }

  return {
    status,
    loading,
    initialized,
    authenticated,
    load,
    setup,
    login,
    logout,
    changePassword,
    expire,
  }
})
