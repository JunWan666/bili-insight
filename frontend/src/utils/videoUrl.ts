const bvPattern = /^BV[0-9A-Za-z]{10}$/i
const avPattern = /^av\d+$/i
const seasonPattern = /^ss[1-9]\d*$/i
const episodePattern = /^ep[1-9]\d*$/i
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
  if (!value) throw new VideoInputError('请输入 Bilibili 视频链接、BV/AV 号或 ss/ep 标识')

  if (bvPattern.test(value) || avPattern.test(value)) {
    return {
      url: `https://www.bilibili.com/video/${value}`,
      partNumber: null,
    }
  }
  if (seasonPattern.test(value) || episodePattern.test(value)) {
    return {
      url: `https://www.bilibili.com/bangumi/play/${value.toLowerCase()}`,
      partNumber: null,
    }
  }

  let parsed: URL
  try {
    parsed = new URL(value)
  } catch {
    throw new VideoInputError('无法识别该链接，请使用 BV/AV 投稿或 ss/ep 番剧链接')
  }

  if (parsed.protocol !== 'https:') {
    throw new VideoInputError('为保护本机网络安全，只支持 HTTPS 视频链接')
  }
  if (!allowedHosts.has(parsed.hostname.toLowerCase())) {
    throw new VideoInputError('无法识别该链接，请使用 bilibili.com 的视频或番剧链接')
  }
  const pathSegments = parsed.pathname.split('/').filter(Boolean)
  const isVideo = pathSegments[0]?.toLowerCase() === 'video'
    && Boolean(pathSegments[1])
    && (bvPattern.test(pathSegments[1]!) || avPattern.test(pathSegments[1]!))
  const isBangumi = pathSegments[0]?.toLowerCase() === 'bangumi'
    && pathSegments[1]?.toLowerCase() === 'play'
    && Boolean(pathSegments[2])
    && (seasonPattern.test(pathSegments[2]!) || episodePattern.test(pathSegments[2]!))
  if (!isVideo && !isBangumi) {
    throw new VideoInputError('当前支持普通 BV/AV 投稿和 ss/ep 番剧链接')
  }

  for (const parameter of trackingParameters) parsed.searchParams.delete(parameter)
  for (const parameter of [...parsed.searchParams.keys()]) {
    if (!isVideo || parameter !== 'p') parsed.searchParams.delete(parameter)
  }
  parsed.hash = ''
  parsed.hostname = 'www.bilibili.com'
  parsed.pathname = isBangumi
    ? `/bangumi/play/${pathSegments[2]!.toLowerCase()}`
    : `/video/${pathSegments[1]}`

  const rawPart = isVideo ? parsed.searchParams.get('p') : null
  const partNumber = rawPart && /^\d+$/.test(rawPart) && Number(rawPart) > 0 ? Number(rawPart) : null
  if (rawPart && partNumber === null) parsed.searchParams.delete('p')

  return { url: parsed.toString().replace(/\/$/, ''), partNumber }
}
