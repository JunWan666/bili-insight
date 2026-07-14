import axios, { AxiosError, AxiosHeaders, type AxiosResponse } from 'axios'
import { describe, expect, it } from 'vitest'
import { toApiError } from './errors'

describe('toApiError', () => {
  it('reads the backend error envelope and preserves only its safe action', () => {
    const response: AxiosResponse = {
      data: {
        error: {
          code: 'COOKIE_FORMAT_INVALID',
          message: 'Cookie 文件内容不是有效 JSON',
          action: '请选择浏览器导出的 Cookie JSON 文件',
          requestId: 'req-safe-id',
        },
      },
      status: 400,
      statusText: 'Bad Request',
      headers: new AxiosHeaders(),
      config: { headers: new AxiosHeaders() },
    }
    const result = toApiError(new AxiosError('Request failed', 'ERR_BAD_REQUEST', undefined, undefined, response))
    expect(result).toMatchObject({
      code: 'COOKIE_FORMAT_INVALID',
      message: 'Cookie 文件内容不是有效 JSON',
      action: '请选择浏览器导出的 Cookie JSON 文件',
      requestId: 'req-safe-id',
      status: 400,
    })
  })

  it('normalizes a connection failure into an actionable network error', () => {
    const result = toApiError(new axios.AxiosError('connect ECONNREFUSED'))
    expect(result.code).toBe('NETWORK_ERROR')
    expect(result.message).toContain('连接服务')
  })

  it('turns the diagnostics privacy switch into a safe settings action', () => {
    const response: AxiosResponse = {
      data: { error: { code: 'DIAGNOSTICS_DISABLED' } },
      status: 403,
      statusText: 'Forbidden',
      headers: new AxiosHeaders(),
      config: { headers: new AxiosHeaders() },
    }
    const result = toApiError(
      new AxiosError('Request failed', 'ERR_BAD_REQUEST', undefined, undefined, response),
    )
    expect(result.code).toBe('DIAGNOSTICS_DISABLED')
    expect(result.message).toContain('详细诊断')
    expect(result.action).toContain('设置页')
  })
})
