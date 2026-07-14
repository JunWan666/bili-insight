import axios from 'axios'
import { camelize } from '@/utils/case'
import { toApiError } from './errors'

const baseURL = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')

export const http = axios.create({
  baseURL,
  timeout: 30_000,
  headers: { Accept: 'application/json' },
})

http.interceptors.response.use(
  (response) => {
    response.data = camelize(response.data)
    return response
  },
  (error: unknown) => Promise.reject(toApiError(error)),
)

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
