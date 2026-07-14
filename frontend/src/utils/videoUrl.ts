const bvPattern = /^BV[0-9A-Za-z]{10}$/i
const avPattern = /^av\d+$/i
const allowedHosts = new Set(['bilibili.com', 'www.bilibili.com', 'm.bilibili.com'])
const trackingParameters = ['spm_id_from', 'vd_source', 'from_source', 'share_source', 'share_medium', 'share_plat']

export interface NormalizedVideoInput {
  url: string
  partNumber: number | null
}

export class VideoInputError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'VideoInputError'
  }
}

export function normalizeVideoInput(rawValue: string): NormalizedVideoInput {
  const value = rawValue.trim()
  if (!value) throw new VideoInputError('请输入 Bilibili 视频链接、BV 号或 AV 号')

  if (bvPattern.test(value) || avPattern.test(value)) {
    return {
      url: `https://www.bilibili.com/video/${value}`,
      partNumber: null,
    }
  }

  let parsed: URL
  try {
    parsed = new URL(value)
  } catch {
    throw new VideoInputError('无法识别该链接，请使用普通 BV/AV 视频链接')
  }

  if (parsed.protocol !== 'https:') {
    throw new VideoInputError('为保护本机网络安全，只支持 HTTPS 视频链接')
  }
  if (!allowedHosts.has(parsed.hostname.toLowerCase())) {
    throw new VideoInputError('无法识别该链接，请使用 bilibili.com 的普通 BV/AV 视频链接')
  }
  const pathSegments = parsed.pathname.split('/').filter(Boolean)
  if (pathSegments[0]?.toLowerCase() !== 'video' || !pathSegments[1] || (!bvPattern.test(pathSegments[1]) && !avPattern.test(pathSegments[1]))) {
    throw new VideoInputError('当前仅支持普通投稿视频的 BV/AV 链接')
  }

  for (const parameter of trackingParameters) parsed.searchParams.delete(parameter)
  for (const parameter of [...parsed.searchParams.keys()]) {
    if (parameter !== 'p') parsed.searchParams.delete(parameter)
  }
  parsed.hash = ''
  parsed.hostname = 'www.bilibili.com'
  parsed.pathname = `/video/${pathSegments[1]}`

  const rawPart = parsed.searchParams.get('p')
  const partNumber = rawPart && /^\d+$/.test(rawPart) && Number(rawPart) > 0 ? Number(rawPart) : null
  if (rawPart && partNumber === null) parsed.searchParams.delete('p')

  return { url: parsed.toString().replace(/\/$/, ''), partNumber }
}
