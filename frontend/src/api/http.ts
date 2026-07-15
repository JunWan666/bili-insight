import axios from 'axios'
import { camelize } from '@/utils/case'
import { toApiError } from './errors'

const baseURL = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')
let csrfToken: string | null = null

export const http = axios.create({
  baseURL,
  timeout: 30_000,
  withCredentials: true,
  headers: { Accept: 'application/json' },
})

http.interceptors.request.use((config) => {
  const method = (config.method || 'get').toLowerCase()
  if (csrfToken && !['get', 'head', 'options'].includes(method)) {
    config.headers.set('X-CSRF-Token', csrfToken)
  }
  return config
})

http.interceptors.response.use(
  (response) => {
    response.data = camelize(response.data)
    return response
  },
  (error: unknown) => {
    const payload = axios.isAxiosError(error) ? error.response?.data as { error?: { code?: string } } | undefined : undefined
    if (payload?.error?.code === 'APP_AUTHENTICATION_REQUIRED') {
      const requestUrl = String(error.config?.url || '')
      if (!requestUrl.endsWith('/app-auth/login')) {
        window.dispatchEvent(new CustomEvent('bili-insight:session-expired'))
      }
    }
    return Promise.reject(toApiError(error))
  },
)

export function setCsrfToken(value: string | null): void {
  csrfToken = value
}

export function apiBaseUrl(): string {
  if (/^https?:\/\//.test(baseURL)) return baseURL
  return `${window.location.origin}${baseURL.startsWith('/') ? '' : '/'}${baseURL}`
}

export function unwrap<T>(payload: T | { data: T }): T {
  if (payload && typeof payload === 'object' && 'data' in payload && Object.keys(payload).length === 1) {
    return (payload as { data: T }).data
  }
  return payload as T
}
