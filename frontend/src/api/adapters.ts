import type {
  AccessMode,
  AnalysisAudioContentLabel,
  AnalysisAudioReport,
  AnalysisBasicReport,
  AnalysisCapability,
  AnalysisCanonicalFeature,
  AnalysisEvidence,
  AnalysisListResult,
  AnalysisMediaReport,
  AnalysisRecord,
  AnalysisResultData,
  AnalysisScenesReport,
  AnalysisStatus,
  AnalysisSummaryReport,
  AnalysisTranscript,
  Artifact,
  ArtifactType,
  AuthState,
  AuthStatus,
  CompanionArtifactType,
  CompanionOutcome,
  DownloadBatchCreationResult,
  DownloadCreationResult,
  Job,
  JobEvent,
  MediaStream,
  PageResult,
  ParsedVideoResult,
  PreviewSession,
  StreamCollection,
  StreamVerificationResult,
  StorageStatus,
  VideoDetail,
  VideoPart,
  VideoRights,
  VideoStatistics,
} from '@/types/api'

type UnknownRecord = Record<string, unknown>

function record(value: unknown): UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? (value as UnknownRecord) : {}
}

function stringValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : value == null ? fallback : String(value)
}

function nullableString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function numberValue(value: unknown, fallback = 0): number {
  const converted = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(converted) ? converted : fallback
}

function nullableNumber(value: unknown): number | null {
  if (value == null || value === '') return null
  const converted = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(converted) ? converted : null
}

function booleanValue(value: unknown): boolean {
  return value === true
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function pick(source: UnknownRecord, ...keys: string[]): unknown {
  for (const key of keys) {
    if (key in source) return source[key]
  }
  return undefined
}

function records(value: unknown): UnknownRecord[] {
  return Array.isArray(value) ? value.map(record) : []
}

function actualMode(value: unknown): 'anonymous' | 'authenticated' {
  return value === 'authenticated' ? 'authenticated' : 'anonymous'
}

function normalizeStatistics(value: unknown): VideoStatistics {
  const source = record(value)
  return {
    views: nullableNumber(source.views ?? source.view),
    likes: nullableNumber(source.likes ?? source.like),
    favorites: nullableNumber(source.favorites ?? source.favorite),
    danmaku: nullableNumber(source.danmaku),
    coins: nullableNumber(source.coins ?? source.coin),
    shares: nullableNumber(source.shares ?? source.share),
  }
}

function normalizeRights(value: unknown): VideoRights {
  const source = record(value)
  const copyrightCode = nullableNumber(source.copyrightCode ?? source.copyright_code ?? source.copyright)
  const copyright = nullableString(source.copyright)
  return {
    copyright:
      copyright ?? (copyrightCode === 1 ? '原创' : copyrightCode === 2 ? '转载' : null),
    isPaid: booleanValue(
      source.isPaid ?? source.paid ?? source.ugcPay ?? source.ugc_pay ?? source.arcPay,
    ),
    isPremiumOnly: booleanValue(
      source.isPremiumOnly ?? source.premiumOnly ?? source.vipOnly ?? source.vip_only,
    ),
  }
}

function normalizePart(value: unknown, videoId: string): VideoPart {
  const source = record(value)
  return {
    id: stringValue(source.id ?? source.cid),
    videoId: stringValue(source.videoId, videoId),
    cid: stringValue(source.cid),
    pageNumber: numberValue(source.pageNumber ?? source.page, 1),
    title: stringValue(source.title, '未命名分 P'),
    duration: numberValue(source.duration),
  }
}

function normalizeVideo(value: unknown, context: UnknownRecord = {}): VideoDetail {
  const source = record(value)
  const access = record(context.access ?? source.access)
  const id = stringValue(source.id ?? source.bvid)
  const rawParts = Array.isArray(source.parts) ? source.parts : []
  return {
    id,
    provider: stringValue(source.provider, 'bilibili'),
    bvid: stringValue(source.bvid),
    aid: source.aid == null ? null : stringValue(source.aid),
    title: stringValue(source.title, '未命名视频'),
    description: stringValue(source.description),
    coverUrl: stringValue(source.coverUrl),
    ownerName: stringValue(source.ownerName, '未知 UP 主'),
    duration: numberValue(source.duration),
    publishedAt: nullableString(source.publishedAt),
    parsedAt: stringValue(context.sourceTime ?? source.parsedAt, new Date(0).toISOString()),
    fromCache: booleanValue(context.cacheHit ?? source.fromCache),
    accessModeUsed: actualMode(access.actualMode ?? source.accessModeUsed),
    authAvailable: booleanValue(access.hasCredentials ?? source.authAvailable),
    normalizedUrl: stringValue(context.normalizedUrl ?? source.normalizedUrl),
    selectedPartId: nullableString(context.selectedPartId ?? source.selectedPartId),
    tags: strings(source.tags),
    statistics: normalizeStatistics(source.stats ?? source.statistics),
    rights: normalizeRights(source.rights),
    parts: rawParts.map((part) => normalizePart(part, id)),
  }
}

function normalizeStream(value: unknown, partId: string): MediaStream {
  const source = record(value)
  const compatibility = record(source.compatibility)
  const mimeType = nullableString(source.mimeType ?? source.mime_type)
  const codecString = nullableString(source.codecString ?? source.codec_string)
  const compatibilityNote =
    nullableString(source.compatibilityNote) ??
    nullableString(compatibility.note) ??
    (typeof source.compatibility === 'string' ? source.compatibility : null)
  const rawRequirement = stringValue(
    source.accessRequirement ?? source.access_requirement,
    booleanValue(source.premiumRequired)
      ? 'premium'
      : booleanValue(source.authRequired)
        ? 'login'
        : 'none',
  )
  const accessRequirement = ['none', 'login', 'premium', 'special'].includes(rawRequirement)
    ? (rawRequirement as MediaStream['accessRequirement'])
    : 'special'
  return {
    id: stringValue(source.id),
    partId: stringValue(source.partId, partId),
    kind: source.kind === 'audio' ? 'audio' : 'video',
    qualityCode: stringValue(source.qualityCode),
    qualityLabel: stringValue(source.qualityLabel, '未知规格'),
    codec: stringValue(source.codec, '未知'),
    container: stringValue(source.container, '未知'),
    width: nullableNumber(source.width),
    height: nullableNumber(source.height),
    fps: nullableNumber(source.fps),
    bitrate: nullableNumber(source.bitrate),
    hdrType: nullableString(source.hdrType),
    audioChannels: nullableNumber(source.audioChannels),
    sampleRate: nullableNumber(source.sampleRate),
    estimatedSize: nullableNumber(source.estimatedSize),
    authRequired: booleanValue(source.authRequired),
    premiumRequired: accessRequirement === 'premium' || booleanValue(source.premiumRequired),
    accessRequirement,
    verifiedAt: nullableString(source.verifiedAt),
    compatibleDevices: strings(source.compatibleDevices ?? compatibility.devices),
    compatibilityNote,
    mimeType,
    codecString,
    previewSupported: source.previewSupported === true || Boolean(
      mimeType && codecString && (source.initializationRange || source.initialization_range),
    ),
  }
}

export function normalizeStreams(value: unknown, accessContext?: unknown): StreamCollection {
  const source = record(value)
  const access = { ...record(accessContext), ...record(source.access) }
  const partId = stringValue(source.partId)
  const rawVideos = Array.isArray(source.video) ? source.video : Array.isArray(source.videos) ? source.videos : []
  const rawAudios = Array.isArray(source.audio) ? source.audio : Array.isArray(source.audios) ? source.audios : []
  return {
    partId,
    accessModeUsed: actualMode(access.actualMode ?? source.accessModeUsed),
    authAvailable: booleanValue(access.hasCredentials ?? source.authAvailable),
    parsedAt: stringValue(source.fetchedAt ?? source.parsedAt, new Date(0).toISOString()),
    videos: rawVideos.map((stream) => normalizeStream(stream, partId)),
    audios: rawAudios.map((stream) => normalizeStream(stream, partId)),
  }
}

export function normalizeStreamVerification(value: unknown): StreamVerificationResult {
  const source = record(value)
  return {
    streamId: stringValue(source.streamId),
    verifiedAt: stringValue(source.verifiedAt),
  }
}

export function normalizePreviewSession(value: unknown): PreviewSession {
  const source = record(value)
  const video = record(source.video)
  const audio = record(source.audio)
  return {
    id: stringValue(source.id),
    manifestUrl: stringValue(source.manifestUrl ?? source.manifest_url),
    expiresAt: stringValue(source.expiresAt ?? source.expires_at),
    duration: numberValue(source.duration),
    video: {
      streamId: stringValue(video.streamId ?? video.stream_id),
      mimeType: stringValue(video.mimeType ?? video.mime_type),
      codecString: stringValue(video.codecString ?? video.codec_string),
    },
    audio: source.audio == null ? null : {
      streamId: stringValue(audio.streamId ?? audio.stream_id),
      mimeType: stringValue(audio.mimeType ?? audio.mime_type),
      codecString: stringValue(audio.codecString ?? audio.codec_string),
    },
  }
}

export function normalizeParseResponse(value: unknown): ParsedVideoResult {
  const source = record(value)
  if ('video' in source) {
    return {
      video: normalizeVideo(source.video, source),
      streams: source.streams ? normalizeStreams(source.streams, source.access) : null,
    }
  }
  return { video: normalizeVideo(source), streams: null }
}

export function normalizeVideoResponse(value: unknown): VideoDetail {
  const source = record(value)
  return 'video' in source ? normalizeVideo(source.video, source) : normalizeVideo(source)
}

export function normalizeAuthStatus(value: unknown): AuthStatus {
  const source = record(value)
  const loggedIn = booleanValue(source.loggedIn ?? source.isAuthenticated)
  const premium = booleanValue(source.premium ?? source.isPremium)
  const rawStatus = stringValue(source.status)
  let status: AuthState
  if (rawStatus === 'validating') status = 'validating'
  else if (['expired', 'invalid'].includes(rawStatus)) status = 'expired'
  else if (rawStatus === 'error') status = 'error'
  else if (premium) status = 'premium'
  else if (loggedIn) status = 'authenticated'
  else status = 'anonymous'
  return {
    status,
    isAuthenticated: loggedIn,
    isPremium: premium,
    maskedAccountName: nullableString(source.maskedAccountName),
    membershipType: nullableString(source.membershipType),
    cookieExpiresAt: nullableString(source.cookieExpiresAt),
    lastValidatedAt: nullableString(source.lastValidatedAt),
    remembered: source.persistence === 'local' || booleanValue(source.remembered),
    message: nullableString(source.message),
  }
}

export function requestedModeFromParse(value: unknown): AccessMode {
  const source = record(value)
  const access = record(source.access)
  const mode = access.requestedMode
  return mode === 'anonymous' || mode === 'authenticated' ? mode : 'auto'
}

const JOB_STATUSES = new Set<Job['status']>([
  'queued',
  'preparing',
  'running',
  'post_processing',
  'paused',
  'completed',
  'canceled',
  'failed',
])

function normalizeJobStatus(value: unknown): Job['status'] {
  const normalized = stringValue(value) as Job['status']
  return JOB_STATUSES.has(normalized) ? normalized : 'failed'
}

export function normalizeArtifact(value: unknown): Artifact {
  const source = record(value)
  const media = record(source.mediaInfo)
  const hasMediaInfo = Object.keys(media).length > 0
  return {
    id: stringValue(source.id),
    jobId: nullableString(pick(source, 'jobId', 'job_id')),
    videoId: nullableString(pick(source, 'videoId', 'video_id') ?? pick(media, 'videoId', 'video_id')),
    videoTitle: nullableString(pick(source, 'videoTitle', 'video_title') ?? pick(media, 'videoTitle', 'video_title')),
    partId: nullableString(pick(source, 'partId', 'part_id') ?? pick(media, 'partId', 'part_id')),
    partTitle: nullableString(pick(source, 'partTitle', 'part_title') ?? pick(media, 'partTitle', 'part_title')),
    sourceUrl: nullableString(pick(source, 'sourceUrl', 'source_url')),
    jobStatus: nullableJobStatus(pick(source, 'jobStatus', 'job_status')),
    type: normalizeArtifactType(source.type),
    filename: stringValue(source.filename, 'artifact'),
    mimeType: stringValue(pick(source, 'mimeType', 'mime_type'), 'application/octet-stream'),
    size: Math.max(0, numberValue(source.size)),
    checksum: stringValue(source.checksum),
    mediaInfo: hasMediaInfo
      ? {
          duration: nullableNumber(media.duration),
          width: nullableNumber(media.width),
          height: nullableNumber(media.height),
          codec: nullableString(media.codec),
          container: nullableString(media.container),
          analysisId: nullableString(pick(media, 'analysisId', 'analysis_id')),
          analysisFeature: nullableAnalysisFeature(pick(media, 'analysisFeature', 'analysis_feature')),
          partId: nullableString(pick(media, 'partId', 'part_id')),
          sceneIndex: nullableNumber(pick(media, 'sceneIndex', 'scene_index')),
          timestampSeconds: nullableNumber(pick(media, 'timestampSeconds', 'timestamp_seconds')),
          format: nullableString(media.format),
          artifactRole: nullableString(pick(media, 'artifactRole', 'artifact_role')),
          source: nullableString(media.source),
          editedFromAnalysisId: nullableString(pick(media, 'editedFromAnalysisId', 'edited_from_analysis_id')),
          editRootAnalysisId: nullableString(pick(media, 'editRootAnalysisId', 'edit_root_analysis_id')),
          editRevision: nullableNumber(pick(media, 'editRevision', 'edit_revision')),
        }
      : null,
    createdAt: stringValue(pick(source, 'createdAt', 'created_at'), new Date(0).toISOString()),
    expiresAt: nullableString(pick(source, 'expiresAt', 'expires_at')),
    retained: booleanValue(source.retained),
    protected: booleanValue(source.protected),
    retentionReason: nullableString(pick(source, 'retentionReason', 'retention_reason')),
    retainedAt: nullableString(pick(source, 'retainedAt', 'retained_at')),
  }
}

function nullableJobStatus(value: unknown): Job['status'] | null {
  const status = nullableString(value) as Job['status'] | null
  return status && JOB_STATUSES.has(status) ? status : null
}

const COMPANION_ARTIFACT_TYPES = new Set<CompanionArtifactType>(['cover', 'subtitle', 'danmaku', 'metadata'])
const COMPANION_OUTCOMES = new Set<CompanionOutcome>(['completed', 'not_available', 'failed'])

function normalizeCompanionOutcomes(value: unknown): Job['companionOutcomes'] {
  const outcomes: Job['companionOutcomes'] = {}
  for (const [rawType, rawOutcome] of Object.entries(record(value))) {
    if (
      COMPANION_ARTIFACT_TYPES.has(rawType as CompanionArtifactType)
      && COMPANION_OUTCOMES.has(rawOutcome as CompanionOutcome)
    ) {
      outcomes[rawType as CompanionArtifactType] = rawOutcome as CompanionOutcome
    }
  }
  return outcomes
}

const ARTIFACT_TYPES = new Set<ArtifactType>([
  'video',
  'audio',
  'cover',
  'subtitle',
  'danmaku',
  'transcript',
  'keyframe',
  'report',
  'metadata',
  'archive',
])

function normalizeArtifactType(value: unknown): ArtifactType {
  const type = stringValue(value, 'report').toLowerCase()
  if (ARTIFACT_TYPES.has(type as ArtifactType)) return type as ArtifactType
  if (type.includes('keyframe')) return 'keyframe'
  if (type.includes('subtitle')) return 'subtitle'
  if (type.includes('danmaku')) return 'danmaku'
  if (type.includes('asr') || type.includes('ocr') || type.includes('transcript')) return 'transcript'
  if (type.includes('cover')) return 'cover'
  if (type.includes('metadata')) return 'metadata'
  if (type.includes('report') || type.includes('manifest')) return 'report'
  return 'report'
}

export function normalizeJob(value: unknown): Job {
  const outer = record(value)
  const source = Object.keys(record(outer.job)).length ? record(outer.job) : outer
  const input = record(source.input)
  const runtime = record(source.runtime)
  const artifacts = Array.isArray(source.artifacts) ? source.artifacts : []
  const explicitArtifactIds = strings(source.artifactIds)
  const companionOutcomes = normalizeCompanionOutcomes(source.companionOutcomes ?? source.companion_outcomes)
  return {
    id: stringValue(source.id),
    type: stringValue(source.type, 'download') as Job['type'],
    status: normalizeJobStatus(source.status),
    phase: stringValue(source.phase, 'queued'),
    progress: Math.max(0, Math.min(100, numberValue(source.progress))),
    videoId: nullableString(source.videoId ?? input.videoId),
    videoTitle: nullableString(source.videoTitle ?? input.videoTitle),
    partTitle: nullableString(source.partTitle ?? input.partTitle),
    sourceUrl: nullableString(source.sourceUrl ?? source.source_url ?? input.officialSource),
    reused: booleanValue(source.reused ?? outer.reused),
    speedBytesPerSecond: nullableNumber(source.speedBytesPerSecond ?? runtime.speedBytesPerSecond),
    etaSeconds: nullableNumber(source.etaSeconds ?? runtime.etaSeconds),
    errorCode: nullableString(source.errorCode),
    errorMessage: nullableString(source.errorMessage),
    retryCount: Math.max(0, numberValue(source.retryCount)),
    cancelRequested: booleanValue(source.cancelRequested),
    createdAt: stringValue(source.createdAt, new Date(0).toISOString()),
    startedAt: nullableString(source.startedAt),
    finishedAt: nullableString(source.finishedAt),
    artifactIds: explicitArtifactIds.length
      ? explicitArtifactIds
      : artifacts.map((artifact) => stringValue(record(artifact).id)).filter(Boolean),
    companionOutcomes,
    hasWarnings: booleanValue(source.hasWarnings ?? source.has_warnings)
      || Object.values(companionOutcomes).includes('failed'),
  }
}

export function normalizeDownloadCreation(value: unknown): DownloadCreationResult {
  const source = record(value)
  return {
    job: normalizeJob(Object.keys(record(source.job)).length ? source.job : source),
    reused: booleanValue(source.reused),
  }
}

export function normalizeDownloadBatchCreation(value: unknown): DownloadBatchCreationResult {
  const source = record(value)
  const items = records(source.items).map(normalizeDownloadCreation)
  const actualReused = items.filter((item) => item.reused).length
  return {
    items,
    createdCount: items.length - actualReused,
    reusedCount: actualReused,
  }
}

export function normalizeJobEvent(value: unknown): JobEvent {
  const source = record(value)
  const nested = record(source.job)
  if (Object.keys(nested).length) {
    const job = normalizeJob(nested)
    return {
      jobId: job.id,
      status: job.status,
      phase: job.phase,
      progress: job.progress,
      speedBytesPerSecond: job.speedBytesPerSecond,
      etaSeconds: job.etaSeconds,
      errorCode: job.errorCode,
      errorMessage: job.errorMessage,
      companionOutcomes: job.companionOutcomes,
      hasWarnings: job.hasWarnings,
      occurredAt: stringValue(source.emittedAt, job.finishedAt ?? job.startedAt ?? job.createdAt),
    }
  }
  const companionOutcomes = normalizeCompanionOutcomes(source.companionOutcomes ?? source.companion_outcomes)
  return {
    jobId: stringValue(source.jobId),
    status: normalizeJobStatus(source.status),
    phase: stringValue(source.phase),
    progress: Math.max(0, Math.min(100, numberValue(source.progress))),
    speedBytesPerSecond: nullableNumber(source.speedBytesPerSecond),
    etaSeconds: nullableNumber(source.etaSeconds),
    errorCode: nullableString(source.errorCode),
    errorMessage: nullableString(source.errorMessage),
    companionOutcomes,
    hasWarnings: booleanValue(source.hasWarnings ?? source.has_warnings)
      || Object.values(companionOutcomes).includes('failed'),
    occurredAt: stringValue(source.occurredAt ?? source.emittedAt, new Date(0).toISOString()),
  }
}

export function normalizeJobList(
  value: unknown,
  requestedPage: number,
  requestedPageSize: number,
): PageResult<Job> {
  const source = record(value)
  const rawItems = Array.isArray(value)
    ? value
    : Array.isArray(source.items)
      ? source.items
      : []
  const pageSize = Math.max(1, numberValue(source.pageSize ?? source.limit, requestedPageSize))
  const offset = Math.max(0, numberValue(source.offset, (requestedPage - 1) * pageSize))
  return {
    items: rawItems.map(normalizeJob),
    total: Math.max(0, numberValue(source.total, rawItems.length)),
    page: Math.max(1, numberValue(source.page, Math.floor(offset / pageSize) + 1)),
    pageSize,
  }
}

export function normalizeArtifactList(
  value: unknown,
  requestedPage: number,
  requestedPageSize: number,
): PageResult<Artifact> {
  const source = record(value)
  const rawItems = Array.isArray(value)
    ? value
    : Array.isArray(source.items)
      ? source.items
      : []
  const pageSize = Math.max(1, numberValue(source.pageSize ?? source.limit, requestedPageSize))
  const offset = Math.max(0, numberValue(source.offset, (requestedPage - 1) * pageSize))
  return {
    items: rawItems.map(normalizeArtifact),
    total: Math.max(0, numberValue(source.total, rawItems.length)),
    page: Math.max(1, numberValue(source.page, Math.floor(offset / pageSize) + 1)),
    pageSize,
  }
}

export function normalizeStorageStatus(value: unknown): StorageStatus {
  const source = record(value)
  return {
    artifactBytes: Math.max(0, numberValue(source.artifactBytes ?? source.artifact_bytes)),
    freeBytes: Math.max(0, numberValue(source.freeBytes ?? source.free_bytes)),
    totalBytes: Math.max(0, numberValue(source.totalBytes ?? source.total_bytes)),
  }
}

const ANALYSIS_FEATURES = new Set<AnalysisCanonicalFeature>([
  'basic',
  'media',
  'audio',
  'subtitles',
  'asr',
  'ocr',
  'scenes',
  'summary',
])
const ANALYSIS_STATUSES = new Set<AnalysisStatus>(['running', 'completed', 'failed', 'canceled'])

function normalizeAnalysisFeature(value: unknown): AnalysisCanonicalFeature {
  const feature = stringValue(value)
  if (feature === 'metadata') return 'basic'
  return ANALYSIS_FEATURES.has(feature as AnalysisCanonicalFeature)
    ? (feature as AnalysisCanonicalFeature)
    : 'basic'
}

function nullableAnalysisFeature(value: unknown): AnalysisCanonicalFeature | null {
  const feature = nullableString(value)
  if (!feature) return null
  if (feature === 'metadata') return 'basic'
  return ANALYSIS_FEATURES.has(feature as AnalysisCanonicalFeature)
    ? (feature as AnalysisCanonicalFeature)
    : null
}

function normalizeAnalysisStatus(value: unknown): AnalysisStatus {
  const status = stringValue(value) as AnalysisStatus
  return ANALYSIS_STATUSES.has(status) ? status : 'failed'
}

function normalizeAnalysisFailure(value: unknown): AnalysisResultData['error'] {
  const source = record(value)
  const message = nullableString(pick(source, 'message', 'userMessage', 'user_message'))
  if (!message) return null
  return {
    code: stringValue(pick(source, 'code', 'reasonCode', 'reason_code'), 'ANALYSIS_STEP_FAILED'),
    message,
    action: nullableString(pick(source, 'action', 'recommendedAction', 'recommended_action')),
  }
}

function normalizeKeyframeStatistics(value: unknown): AnalysisVideoTechnicalInfo['keyframes'] {
  const source = record(value)
  if (!Object.keys(source).length) return null
  return {
    count: Math.max(0, numberValue(source.count)),
    timestampsSeconds: recordsOrNumbers(pick(source, 'timestampsSeconds', 'timestamps_seconds')),
    averageIntervalSeconds: nullableNumber(pick(source, 'averageIntervalSeconds', 'average_interval_seconds')),
    minimumIntervalSeconds: nullableNumber(pick(source, 'minimumIntervalSeconds', 'minimum_interval_seconds')),
    maximumIntervalSeconds: nullableNumber(pick(source, 'maximumIntervalSeconds', 'maximum_interval_seconds')),
    truncated: booleanValue(source.truncated),
  }
}

function recordsOrNumbers(value: unknown): number[] {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => nullableNumber(item))
    .filter((item): item is number => item !== null)
}

function normalizeMediaReport(value: unknown): AnalysisMediaReport | null {
  const source = record(value)
  if (!Object.keys(source).length) return null
  const containerSource = record(source.container)
  const rawFormatNames = pick(containerSource, 'formatNames', 'format_names')
  const videoStreams = records(pick(source, 'videoStreams', 'video_streams')).map((stream) => ({
    index: Math.max(0, numberValue(stream.index)),
    codecName: nullableString(pick(stream, 'codecName', 'codec_name')),
    codecLongName: nullableString(pick(stream, 'codecLongName', 'codec_long_name')),
    profile: nullableString(stream.profile),
    level: nullableNumber(stream.level),
    width: nullableNumber(stream.width),
    height: nullableNumber(stream.height),
    pixelFormat: nullableString(pick(stream, 'pixelFormat', 'pixel_format')),
    averageFrameRate: nullableNumber(pick(stream, 'averageFrameRate', 'average_frame_rate')),
    realFrameRate: nullableNumber(pick(stream, 'realFrameRate', 'real_frame_rate')),
    durationSeconds: nullableNumber(pick(stream, 'durationSeconds', 'duration_seconds')),
    bitRate: nullableNumber(pick(stream, 'bitRate', 'bit_rate')),
    frameCount: nullableNumber(pick(stream, 'frameCount', 'frame_count')),
    colorRange: nullableString(pick(stream, 'colorRange', 'color_range')),
    colorSpace: nullableString(pick(stream, 'colorSpace', 'color_space')),
    colorTransfer: nullableString(pick(stream, 'colorTransfer', 'color_transfer')),
    colorPrimaries: nullableString(pick(stream, 'colorPrimaries', 'color_primaries')),
    hdrType: nullableString(pick(stream, 'hdrType', 'hdr_type')),
    keyframes: normalizeKeyframeStatistics(stream.keyframes),
  }))
  const audioStreams = records(pick(source, 'audioStreams', 'audio_streams')).map((stream) => ({
    index: Math.max(0, numberValue(stream.index)),
    codecName: nullableString(pick(stream, 'codecName', 'codec_name')),
    codecLongName: nullableString(pick(stream, 'codecLongName', 'codec_long_name')),
    profile: nullableString(stream.profile),
    sampleFormat: nullableString(pick(stream, 'sampleFormat', 'sample_format')),
    sampleRateHz: nullableNumber(pick(stream, 'sampleRateHz', 'sample_rate_hz')),
    channels: nullableNumber(stream.channels),
    channelLayout: nullableString(pick(stream, 'channelLayout', 'channel_layout')),
    durationSeconds: nullableNumber(pick(stream, 'durationSeconds', 'duration_seconds')),
    bitRate: nullableNumber(pick(stream, 'bitRate', 'bit_rate')),
    bitsPerSample: nullableNumber(pick(stream, 'bitsPerSample', 'bits_per_sample')),
  }))
  return {
    probeName: nullableString(pick(source, 'probeName', 'probe_name')),
    probeVersion: nullableString(pick(source, 'probeVersion', 'probe_version')),
    container: Object.keys(containerSource).length
      ? {
          formatNames: Array.isArray(rawFormatNames)
            ? strings(rawFormatNames)
            : stringValue(rawFormatNames).split(',').map((item) => item.trim()).filter(Boolean),
          formatLongName: nullableString(pick(containerSource, 'formatLongName', 'format_long_name')),
          durationSeconds: nullableNumber(pick(containerSource, 'durationSeconds', 'duration_seconds')),
          sizeBytes: nullableNumber(pick(containerSource, 'sizeBytes', 'size_bytes')),
          bitRate: nullableNumber(pick(containerSource, 'bitRate', 'bit_rate')),
          startTimeSeconds: nullableNumber(pick(containerSource, 'startTimeSeconds', 'start_time_seconds')),
        }
      : null,
    videoStreams,
    audioStreams,
    subtitleStreams: records(pick(source, 'subtitleStreams', 'subtitle_streams')).map((stream) => ({
      index: Math.max(0, numberValue(stream.index)),
      codecName: nullableString(pick(stream, 'codecName', 'codec_name')),
      language: nullableString(stream.language),
      title: nullableString(stream.title),
    })),
    chapters: records(source.chapters).map((chapter) => ({
      index: Math.max(0, numberValue(chapter.index)),
      startSeconds: Math.max(0, numberValue(pick(chapter, 'startSeconds', 'start_seconds'))),
      endSeconds: Math.max(0, numberValue(pick(chapter, 'endSeconds', 'end_seconds'))),
      title: nullableString(chapter.title),
    })),
    warnings: strings(source.warnings),
  }
}

function normalizeAudioReport(value: unknown): AnalysisAudioReport | null {
  const source = record(value)
  if (!Object.keys(source).length) return null
  const spectrumSource = record(pick(source, 'spectrumOverview', 'spectrum_overview'))
  const classificationSource = record(pick(source, 'contentClassification', 'content_classification'))
  return {
    analyzerName: nullableString(pick(source, 'analyzerName', 'analyzer_name')),
    analyzerVersion: nullableString(pick(source, 'analyzerVersion', 'analyzer_version')),
    streamIndex: Math.max(0, numberValue(pick(source, 'streamIndex', 'stream_index'))),
    integratedLoudnessLufs: nullableNumber(pick(source, 'integratedLoudnessLufs', 'integrated_loudness_lufs')),
    loudnessRangeLu: nullableNumber(pick(source, 'loudnessRangeLu', 'loudness_range_lu')),
    samplePeakDbfs: nullableNumber(pick(source, 'samplePeakDbfs', 'sample_peak_dbfs')),
    truePeakDbfs: nullableNumber(pick(source, 'truePeakDbfs', 'true_peak_dbfs')),
    meanVolumeDb: nullableNumber(pick(source, 'meanVolumeDb', 'mean_volume_db')),
    silenceThresholdDb: nullableNumber(pick(source, 'silenceThresholdDb', 'silence_threshold_db')),
    minimumSilenceSeconds: nullableNumber(pick(source, 'minimumSilenceSeconds', 'minimum_silence_seconds')),
    silenceIntervals: records(pick(source, 'silenceIntervals', 'silence_intervals')).map((interval) => ({
      startSeconds: Math.max(0, numberValue(pick(interval, 'startSeconds', 'start_seconds'))),
      endSeconds: Math.max(0, numberValue(pick(interval, 'endSeconds', 'end_seconds'))),
      durationSeconds: Math.max(0, numberValue(pick(interval, 'durationSeconds', 'duration_seconds'))),
    })),
    loudnessCurve: records(pick(source, 'loudnessCurve', 'loudness_curve')).map((point) => ({
      timestampSeconds: Math.max(0, numberValue(pick(point, 'timestampSeconds', 'timestamp_seconds'))),
      momentaryLufs: nullableNumber(pick(point, 'momentaryLufs', 'momentary_lufs')),
      shortTermLufs: nullableNumber(pick(point, 'shortTermLufs', 'short_term_lufs')),
      integratedLufs: nullableNumber(pick(point, 'integratedLufs', 'integrated_lufs')),
      loudnessRangeLu: nullableNumber(pick(point, 'loudnessRangeLu', 'loudness_range_lu')),
    })),
    spectrumOverview: Object.keys(spectrumSource).length
      ? {
          analyzerName: nullableString(pick(spectrumSource, 'analyzerName', 'analyzer_name')),
          analyzerVersion: nullableString(pick(spectrumSource, 'analyzerVersion', 'analyzer_version')),
          frequencyScale: stringValue(pick(spectrumSource, 'frequencyScale', 'frequency_scale'), 'logarithmic'),
          minimumFrequencyHz: Math.max(0, numberValue(pick(spectrumSource, 'minimumFrequencyHz', 'minimum_frequency_hz'))),
          maximumFrequencyHz: Math.max(0, numberValue(pick(spectrumSource, 'maximumFrequencyHz', 'maximum_frequency_hz'))),
          analyzedDurationSeconds: nullableNumber(pick(spectrumSource, 'analyzedDurationSeconds', 'analyzed_duration_seconds')),
          timeBins: Math.max(0, numberValue(pick(spectrumSource, 'timeBins', 'time_bins'))),
          frequencyBins: Math.max(0, numberValue(pick(spectrumSource, 'frequencyBins', 'frequency_bins'))),
          dominantFrequencyHz: nullableNumber(pick(spectrumSource, 'dominantFrequencyHz', 'dominant_frequency_hz')),
          spectralCentroidHz: nullableNumber(pick(spectrumSource, 'spectralCentroidHz', 'spectral_centroid_hz')),
          bands: records(spectrumSource.bands).map((band) => ({
            key: stringValue(band.key),
            label: stringValue(band.label),
            minimumFrequencyHz: Math.max(0, numberValue(pick(band, 'minimumFrequencyHz', 'minimum_frequency_hz'))),
            maximumFrequencyHz: Math.max(0, numberValue(pick(band, 'maximumFrequencyHz', 'maximum_frequency_hz'))),
            relativeMagnitude: Math.max(0, Math.min(1, numberValue(pick(band, 'relativeMagnitude', 'relative_magnitude')))),
            magnitudeShare: Math.max(0, Math.min(1, numberValue(pick(band, 'magnitudeShare', 'magnitude_share')))),
            peakMagnitude: Math.max(0, Math.min(1, numberValue(pick(band, 'peakMagnitude', 'peak_magnitude')))),
          })).filter((band) => band.key.length > 0),
          disclaimer: stringValue(spectrumSource.disclaimer),
        }
      : null,
    contentClassification: Object.keys(classificationSource).length
      ? {
          classifierName: nullableString(pick(classificationSource, 'classifierName', 'classifier_name')),
          classifierVersion: nullableString(pick(classificationSource, 'classifierVersion', 'classifier_version')),
          heuristic: classificationSource.heuristic === undefined
            ? true
            : booleanValue(classificationSource.heuristic),
          segments: records(classificationSource.segments).map((segment, index) => {
            const rawLabel = stringValue(segment.label, 'mixed_or_uncertain')
            const label = ['silence', 'speech_likely', 'music_likely', 'mixed_or_uncertain'].includes(rawLabel)
              ? (rawLabel as AnalysisAudioContentLabel)
              : 'mixed_or_uncertain'
            return {
              index: Math.max(0, numberValue(segment.index, index)),
              startSeconds: Math.max(0, numberValue(pick(segment, 'startSeconds', 'start_seconds'))),
              endSeconds: Math.max(0, numberValue(pick(segment, 'endSeconds', 'end_seconds'))),
              label,
              confidence: clampConfidence(segment.confidence) ?? 0,
              speechBandRatio: Math.max(0, Math.min(1, numberValue(pick(segment, 'speechBandRatio', 'speech_band_ratio')))),
              spectralFlatness: Math.max(0, Math.min(1, numberValue(pick(segment, 'spectralFlatness', 'spectral_flatness')))),
              explanation: stringValue(segment.explanation),
            }
          }),
          disclaimer: stringValue(classificationSource.disclaimer),
          limitations: strings(classificationSource.limitations),
        }
      : null,
    warnings: strings(source.warnings),
  }
}

function normalizeTranscript(value: unknown, provenanceValue?: unknown): AnalysisTranscript | null {
  const source = record(value)
  if (!Object.keys(source).length) return null
  const provenance = record(provenanceValue)
  return {
    language: stringValue(source.language, 'und'),
    source: stringValue(source.source, 'unknown'),
    modelName: nullableString(pick(source, 'modelName', 'model_name')),
    modelVersion: nullableString(pick(source, 'modelVersion', 'model_version')),
    generatedAt: nullableString(pick(source, 'generatedAt', 'generated_at')),
    warnings: strings(source.warnings),
    segments: records(source.segments).map((segment, index) => ({
      index: Math.max(1, numberValue(segment.index, index + 1)),
      startSeconds: Math.max(0, numberValue(pick(segment, 'startSeconds', 'start_seconds'))),
      endSeconds: Math.max(0, numberValue(pick(segment, 'endSeconds', 'end_seconds'))),
      text: stringValue(segment.text),
      source: stringValue(segment.source, stringValue(source.source, 'unknown')),
      language: stringValue(segment.language, stringValue(source.language, 'und')),
      confidence: clampConfidence(segment.confidence),
      evidenceId: nullableString(pick(segment, 'evidenceId', 'evidence_id')),
    })).filter((segment) => segment.text.length > 0),
    editProvenance: Object.keys(provenance).length
      ? {
          sourceAnalysisId: stringValue(pick(provenance, 'sourceAnalysisId', 'source_analysis_id')),
          rootAnalysisId: stringValue(pick(provenance, 'rootAnalysisId', 'root_analysis_id')),
          revision: Math.max(1, numberValue(provenance.revision, 1)),
          editedAt: nullableString(pick(provenance, 'editedAt', 'edited_at')),
          sourceUpdatedAt: nullableString(pick(provenance, 'sourceUpdatedAt', 'source_updated_at')),
          sourceTranscriptSource: stringValue(pick(provenance, 'sourceTranscriptSource', 'source_transcript_source'), 'unknown'),
        }
      : null,
  }
}

function clampConfidence(value: unknown): number | null {
  const normalized = nullableNumber(value)
  return normalized === null ? null : Math.max(0, Math.min(1, normalized))
}

function normalizeScenesReport(value: unknown): AnalysisScenesReport | null {
  const root = record(value)
  const source = record(pick(root, 'sceneAnalysis', 'scene_analysis'))
  const keyframeSource = record(pick(root, 'keyframeAnalysis', 'keyframe_analysis'))
  if (!Object.keys(source).length && !Object.keys(keyframeSource).length) return null
  return {
    analyzerName: nullableString(pick(source, 'analyzerName', 'analyzer_name')),
    analyzerVersion: nullableString(pick(source, 'analyzerVersion', 'analyzer_version')),
    threshold: nullableNumber(source.threshold),
    durationSeconds: nullableNumber(pick(source, 'durationSeconds', 'duration_seconds')),
    scenes: records(source.scenes).map((scene, index) => ({
      index: Math.max(0, numberValue(scene.index, index)),
      startSeconds: Math.max(0, numberValue(pick(scene, 'startSeconds', 'start_seconds'))),
      endSeconds: Math.max(0, numberValue(pick(scene, 'endSeconds', 'end_seconds'))),
      durationSeconds: Math.max(0, numberValue(pick(scene, 'durationSeconds', 'duration_seconds'))),
      transitionScore: nullableNumber(pick(scene, 'transitionScore', 'transition_score')),
    })),
    averageSceneLengthSeconds: nullableNumber(pick(source, 'averageSceneLengthSeconds', 'average_scene_length_seconds')),
    sceneDensityPerMinute: nullableNumber(pick(source, 'sceneDensityPerMinute', 'scene_density_per_minute')),
    truncated: booleanValue(source.truncated),
    keyframes: records(pick(keyframeSource, 'artifacts', 'keyframes')).map((keyframe, index) => ({
      index: Math.max(0, numberValue(keyframe.index, index)),
      timestampSeconds: Math.max(0, numberValue(pick(keyframe, 'timestampSeconds', 'timestamp_seconds'))),
      sceneIndex: Math.max(0, numberValue(pick(keyframe, 'sceneIndex', 'scene_index'))),
      filename: stringValue(keyframe.filename, `keyframe-${index + 1}.jpg`),
      sizeBytes: nullableNumber(pick(keyframe, 'sizeBytes', 'size_bytes')),
      sha256: nullableString(keyframe.sha256),
    })),
    warnings: [...new Set([...strings(source.warnings), ...strings(keyframeSource.warnings)])],
  }
}

function normalizeEvidence(value: unknown): AnalysisEvidence | null {
  const source = record(value)
  if (!Object.keys(source).length) return null
  return {
    startSeconds: nullableNumber(pick(source, 'startSeconds', 'start_seconds')),
    endSeconds: nullableNumber(pick(source, 'endSeconds', 'end_seconds')),
    text: stringValue(source.text),
    source: stringValue(source.source, 'unknown'),
    confidence: clampConfidence(source.confidence),
    evidenceId: nullableString(pick(source, 'evidenceId', 'evidence_id')),
    locator: nullableString(source.locator),
    artifactId: nullableString(pick(source, 'artifactId', 'artifact_id')),
  }
}

function normalizeSummaryReport(value: unknown): AnalysisSummaryReport | null {
  const source = record(value)
  if (!Object.keys(source).length) return null
  return {
    summary: stringValue(source.summary),
    summarySentences: records(pick(source, 'summarySentences', 'summary_sentences')).map((item) => ({
      text: stringValue(item.text),
      score: nullableNumber(item.score),
      evidence: normalizeEvidence(item.evidence),
    })).filter((item) => item.text.length > 0),
    keywords: records(source.keywords).map((item) => ({
      keyword: stringValue(item.keyword),
      score: nullableNumber(item.score),
      occurrences: Math.max(0, numberValue(item.occurrences)),
      evidence: records(item.evidence).map(normalizeEvidence).filter((entry): entry is AnalysisEvidence => entry !== null),
    })).filter((item) => item.keyword.length > 0),
    chapters: records(source.chapters).map((item, index) => ({
      index: Math.max(0, numberValue(item.index, index)),
      title: stringValue(item.title, `章节 ${index + 1}`),
      startSeconds: Math.max(0, numberValue(pick(item, 'startSeconds', 'start_seconds'))),
      endSeconds: Math.max(0, numberValue(pick(item, 'endSeconds', 'end_seconds'))),
      summary: stringValue(item.summary),
      keywords: strings(item.keywords),
      evidence: records(item.evidence).map(normalizeEvidence).filter((entry): entry is AnalysisEvidence => entry !== null),
    })),
    topics: records(source.topics).map((item) => ({
      topic: stringValue(item.topic),
      score: nullableNumber(item.score),
      evidence: records(item.evidence).map(normalizeEvidence).filter((entry): entry is AnalysisEvidence => entry !== null),
    })).filter((item) => item.topic.length > 0),
    entityCandidates: records(pick(source, 'entityCandidates', 'entity_candidates')).map((item) => ({
      name: stringValue(item.name),
      category: stringValue(item.category, 'unknown'),
      evidence: normalizeEvidence(item.evidence),
      limitation: stringValue(item.limitation),
    })).filter((item) => item.name.length > 0),
    emotionTimeline: records(pick(source, 'emotionTimeline', 'emotion_timeline')).map((item) => ({
      startSeconds: Math.max(0, numberValue(pick(item, 'startSeconds', 'start_seconds'))),
      endSeconds: Math.max(0, numberValue(pick(item, 'endSeconds', 'end_seconds'))),
      label: stringValue(item.label),
      score: nullableNumber(item.score),
      evidence: normalizeEvidence(item.evidence),
    })).filter((item) => item.label.length > 0),
    visualEvidence: records(pick(source, 'visualEvidence', 'visual_evidence')).map(normalizeEvidence).filter((entry): entry is AnalysisEvidence => entry !== null),
    semanticCapabilities: records(pick(source, 'semanticCapabilities', 'semantic_capabilities')).map((item) => ({
      name: stringValue(item.name, 'unknown'),
      status: stringValue(item.status, 'unavailable'),
      method: stringValue(item.method, 'unknown'),
      message: stringValue(item.message),
    })),
    coverage: stringValue(source.coverage, 'unknown'),
    modelName: nullableString(pick(source, 'modelName', 'model_name')),
    modelVersion: nullableString(pick(source, 'modelVersion', 'model_version')),
    generatedAt: nullableString(pick(source, 'generatedAt', 'generated_at')),
    inputSources: strings(pick(source, 'inputSources', 'input_sources')),
    inputDetails: record(pick(source, 'inputDetails', 'input_details')),
    inputDigestSha256: nullableString(pick(source, 'inputDigestSha256', 'input_digest_sha256')),
    parameters: record(source.parameters),
    disclaimer: stringValue(source.disclaimer, '自动分析结果可能存在误差，请结合时间戳证据复核。'),
    warnings: strings(source.warnings),
  }
}

function normalizeBasicReport(value: unknown): AnalysisBasicReport | null {
  const source = record(value)
  if (!Object.keys(source).length) return null
  const video = record(source.video)
  const part = record(source.part)
  return {
    generatedAt: nullableString(pick(source, 'generatedAt', 'generated_at')),
    title: stringValue(video.title),
    description: stringValue(video.description),
    ownerName: stringValue(pick(video, 'ownerName', 'owner_name')),
    durationSeconds: nullableNumber(pick(video, 'durationSeconds', 'duration_seconds')),
    publishedAt: nullableString(pick(video, 'publishedAt', 'published_at')),
    tags: strings(video.tags),
    partTitle: stringValue(part.title),
    pageNumber: nullableNumber(pick(part, 'pageNumber', 'page_number')),
    subtitleAvailability: nullableString(pick(source, 'subtitleAvailability', 'subtitle_availability')),
  }
}

function normalizeAnalysisResult(feature: AnalysisCanonicalFeature, value: unknown): AnalysisResultData {
  const source = record(value)
  const report = record(source.report)
  return {
    artifactIds: strings(pick(source, 'artifactIds', 'artifact_ids')),
    error: normalizeAnalysisFailure(source.error),
    basic: feature === 'basic' ? normalizeBasicReport(source) : null,
    media: feature === 'media' ? normalizeMediaReport(Object.keys(report).length ? report : source) : null,
    audio: feature === 'audio' ? normalizeAudioReport(Object.keys(report).length ? report : source) : null,
    transcript: ['subtitles', 'asr', 'ocr'].includes(feature)
      ? normalizeTranscript(source.document, pick(source, 'editProvenance', 'edit_provenance'))
      : null,
    scenes: feature === 'scenes' ? normalizeScenesReport(source) : null,
    summary: feature === 'summary' ? normalizeSummaryReport(Object.keys(report).length ? report : source) : null,
  }
}

export function normalizeAnalysis(value: unknown): AnalysisRecord {
  const outer = record(value)
  const source = Object.keys(record(outer.analysis)).length ? record(outer.analysis) : outer
  const feature = normalizeAnalysisFeature(source.feature)
  const parameters = record(source.parameters)
  return {
    id: stringValue(source.id),
    videoId: stringValue(pick(source, 'videoId', 'video_id')),
    partId: nullableString(pick(source, 'partId', 'part_id')),
    feature,
    status: normalizeAnalysisStatus(source.status),
    result: normalizeAnalysisResult(feature, source.result),
    modelName: nullableString(pick(source, 'modelName', 'model_name')),
    modelVersion: nullableString(pick(source, 'modelVersion', 'model_version')),
    parameters,
    jobId: nullableString(pick(parameters, 'jobId', 'job_id')),
    createdAt: stringValue(pick(source, 'createdAt', 'created_at'), new Date(0).toISOString()),
    updatedAt: stringValue(pick(source, 'updatedAt', 'updated_at'), new Date(0).toISOString()),
  }
}

export function normalizeAnalysisList(value: unknown): AnalysisListResult {
  const outer = record(value)
  const source = Object.keys(record(outer.data)).length ? record(outer.data) : outer
  const rawItems = Array.isArray(value)
    ? value
    : Array.isArray(source.items)
      ? source.items
      : []
  return {
    items: rawItems.map(normalizeAnalysis),
    total: Math.max(0, numberValue(source.total, rawItems.length)),
    limit: Math.max(1, numberValue(source.limit, Math.max(1, rawItems.length))),
    offset: Math.max(0, numberValue(source.offset)),
  }
}

export function normalizeAnalysisCapabilities(value: unknown): AnalysisCapability[] {
  const outer = record(value)
  const source = Object.keys(record(outer.data)).length ? record(outer.data) : outer
  const rawItems = Array.isArray(value) ? value : Array.isArray(source.items) ? source.items : []
  return rawItems.map((item) => {
    const capability = record(item)
    return {
      feature: normalizeAnalysisFeature(capability.feature),
      component: stringValue(capability.component, 'unknown'),
      available: booleanValue(capability.available),
      version: nullableString(capability.version),
      reasonCode: nullableString(pick(capability, 'reasonCode', 'reason_code')),
      message: stringValue(capability.message, '能力状态未知'),
      action: nullableString(capability.action),
    }
  })
}
