import axios from 'axios'

interface ErrorPayload {
  code?: string
  message?: string
  userMessage?: string
  action?: string
  requestId?: string
  error?: ErrorPayload
  detail?: string | { code?: string; message?: string; userMessage?: string; action?: string; requestId?: string }
}

const knownErrors: Record<string, { message: string; action: string }> = {
  INVALID_LINK: { message: '无法识别该链接，请使用普通 BV/AV 视频链接', action: '返回首页修改链接' },
  INVALID_VIDEO_URL: { message: '无法识别该链接，请使用普通 BV/AV 视频链接', action: '返回首页修改链接' },
  UNSUPPORTED_CONTENT: { message: '当前仅支持普通投稿视频', action: '请更换为普通 BV/AV 视频链接' },
  VIDEO_NOT_FOUND: { message: '视频不存在、已删除或当前不可见', action: '请检查链接或更换视频' },
  REGION_RESTRICTED: { message: '该视频在当前地区不可用', action: '请在 Bilibili 官方页面确认访问权限' },
  PERMISSION_DENIED: { message: '当前身份无权访问该视频', action: '校验 Cookie 或继续匿名使用' },
  AUTHENTICATION_REQUIRED: { message: '当前规格需要有效登录权益', action: '校验 Cookie 或选择较低规格' },
  AUTH_REQUIRED: { message: '当前规格需要有效登录权益', action: '校验 Cookie 或选择较低规格' },
  COOKIE_FORMAT_INVALID: { message: 'Cookie 文件格式无法识别，未保存任何内容', action: '查看格式说明并重新上传' },
  COOKIE_INVALID: { message: 'Cookie 文件格式无法识别，未保存任何内容', action: '查看格式说明并重新上传' },
  COOKIE_EXPIRED: { message: '登录状态已失效，当前已切换为匿名模式', action: '重新上传或继续匿名使用' },
  RISK_CONTROL: { message: 'Bilibili 暂时要求额外验证，本工具不会绕过', action: '稍后重试或在官方页面处理' },
  PLATFORM_RISK_CONTROL: { message: 'Bilibili 暂时要求额外验证，本工具不会绕过', action: '稍后重试或在官方页面处理' },
  UPSTREAM_NETWORK_ERROR: { message: '暂时无法连接 Bilibili', action: '检查网络后稍后重试' },
  AUTH_VALIDATION_ERROR: { message: '登录状态暂时无法验证', action: '原配置已保留，请稍后重新校验' },
  UPLOAD_TOO_LARGE: { message: 'Cookie 文件超过允许的大小', action: '请选择不超过 1 MB 的 JSON 文件' },
  DISK_FULL: { message: '可用空间不足，任务已停止', action: '清理产物或调整存储设置' },
  FFMPEG_FAILED: { message: '音视频处理失败', action: '查看脱敏诊断并重试' },
  MODEL_UNAVAILABLE: { message: '对应分析模型未安装或资源不足', action: '调整分析选项或继续下载' },
  DIAGNOSTICS_DISABLED: { message: '详细诊断已在隐私设置中关闭', action: '如需查看组件指标，请在设置页临时启用诊断；基础健康检查仍保持可用' },
  NETWORK_ERROR: { message: '暂时无法连接服务', action: '确认后端已启动并稍后重试' },
}

function objectPayload(value: unknown): ErrorPayload {
  return typeof value === 'object' && value !== null ? (value as ErrorPayload) : {}
}

export class ApiError extends Error {
  readonly code: string
  readonly action: string
  readonly status: number | null
  readonly requestId: string | null

  constructor(options: { code: string; message: string; action: string; status?: number; requestId?: string }) {
    super(options.message)
    this.name = 'ApiError'
    this.code = options.code
    this.action = options.action
    this.status = options.status ?? null
    this.requestId = options.requestId ?? null
  }
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error
  if (!axios.isAxiosError(error)) {
    return new ApiError({ code: 'UNKNOWN_ERROR', message: '操作未能完成', action: '请稍后重试' })
  }

  const payload = objectPayload(error.response?.data)
  const nested = objectPayload(payload.error)
  const detail = objectPayload(payload.detail)
  const code = payload.code ?? nested.code ?? detail.code ?? (error.response ? `HTTP_${error.response.status}` : 'NETWORK_ERROR')
  const fallback = knownErrors[code] ?? knownErrors.NETWORK_ERROR
  const serverMessage = payload.userMessage ?? nested.userMessage ?? detail.userMessage ?? payload.message ?? nested.message ?? detail.message
  const safeMessage = serverMessage && serverMessage.length <= 240 ? serverMessage : fallback?.message
  return new ApiError({
    code,
    message: safeMessage ?? '操作未能完成',
    action: payload.action ?? nested.action ?? detail.action ?? fallback?.action ?? '请稍后重试',
    status: error.response?.status,
    requestId: payload.requestId ?? nested.requestId ?? detail.requestId ?? error.response?.headers['x-request-id'],
  })
}
