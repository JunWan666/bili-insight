export type AccessMode = 'auto' | 'anonymous' | 'authenticated'

export interface AppAuthStatus {
  initialized: boolean
  authenticated: boolean
  username: string | null
  csrfToken: string | null
  sessionExpiresAt: string | null
}

export interface AppSetupRequest {
  username: string
  password: string
  confirmPassword: string
}

export interface AppLoginRequest {
  username: string
  password: string
}

export interface AppPasswordChangeRequest {
  currentPassword: string
  newPassword: string
  confirmPassword: string
}

export type AuthState =
  | 'anonymous'
  | 'validating'
  | 'authenticated'
  | 'premium'
  | 'expired'
  | 'error'

export interface AuthStatus {
  status: AuthState
  isAuthenticated: boolean
  isPremium: boolean
  maskedAccountName: string | null
  membershipType: string | null
  cookieExpiresAt: string | null
  lastValidatedAt: string | null
  remembered: boolean
  message: string | null
}

export interface VideoStatistics {
  views: number | null
  likes: number | null
  favorites: number | null
  danmaku: number | null
  coins: number | null
  shares: number | null
}

export interface VideoRights {
  copyright: string | null
  isPaid: boolean
  isPremiumOnly: boolean
}

export interface VideoPart {
  id: string
  videoId: string
  cid: string
  pageNumber: number
  title: string
  duration: number
}

export interface VideoDetail {
  id: string
  provider: string
  bvid: string
  aid: string | null
  title: string
  description: string
  coverUrl: string
  ownerName: string
  duration: number
  publishedAt: string | null
  parsedAt: string
  fromCache: boolean
  accessModeUsed: Exclude<AccessMode, 'auto'>
  authAvailable: boolean
  normalizedUrl: string
  selectedPartId: string | null
  tags: string[]
  statistics: VideoStatistics
  rights: VideoRights
  parts: VideoPart[]
}

export interface ParsedVideoResult {
  video: VideoDetail
  streams: StreamCollection | null
}

export type StreamKind = 'video' | 'audio'
export type StreamAccessRequirement = 'none' | 'login' | 'premium' | 'special'

export interface MediaStream {
  id: string
  partId: string
  kind: StreamKind
  qualityCode: string
  qualityLabel: string
  codec: string
  container: string
  width: number | null
  height: number | null
  fps: number | null
  bitrate: number | null
  hdrType: string | null
  audioChannels: number | null
  sampleRate: number | null
  estimatedSize: number | null
  authRequired: boolean
  premiumRequired: boolean
  accessRequirement: StreamAccessRequirement
  verifiedAt: string | null
  compatibleDevices: string[]
  compatibilityNote: string | null
  mimeType?: string | null
  codecString?: string | null
  previewSupported?: boolean
}

export interface StreamCollection {
  partId: string
  accessModeUsed: Exclude<AccessMode, 'auto'>
  authAvailable: boolean
  parsedAt: string
  videos: MediaStream[]
  audios: MediaStream[]
}

export interface StreamVerificationResult {
  streamId: string
  verifiedAt: string
}

export interface PreviewTrack {
  streamId: string
  mimeType: string
  codecString: string
}

export interface PreviewSession {
  id: string
  manifestUrl: string
  expiresAt: string
  duration: number
  video: PreviewTrack
  audio: PreviewTrack | null
}

export interface CreatePreviewRequest {
  videoStreamId: string
  audioStreamId: string | null
  accessMode: Exclude<AccessMode, 'auto'>
}

export type DownloadPreset = 'best_quality' | 'best_compatibility' | 'smallest' | 'audio_only' | 'custom'
export type ProcessingMode = 'copy' | 'transcode'
export type OutputContainer = 'mp4' | 'mkv' | 'm4a' | 'mp3' | 'flac'

export interface CreateDownloadRequest {
  videoId: string
  partId: string
  videoStreamId: string | null
  audioStreamId: string | 'auto' | 'none'
  container: OutputContainer
  processingMode: ProcessingMode
  accessMode: AccessMode
  includeSubtitle: boolean
  includeCover: boolean
  includeMetadata: boolean
  includeDanmaku: boolean
  cleanupTemporary: boolean
  filename: string | null
  reuseExisting: boolean
}

export interface DownloadCreationResult {
  job: Job
  reused: boolean
}

export interface CreateDownloadBatchRequest {
  downloads: CreateDownloadRequest[]
}

export interface DownloadBatchCreationResult {
  items: DownloadCreationResult[]
  createdCount: number
  reusedCount: number
}

export type AnalysisFeature =
  | 'basic'
  | 'metadata'
  | 'media'
  | 'audio'
  | 'subtitles'
  | 'asr'
  | 'ocr'
  | 'scenes'
  | 'summary'

export type AnalysisCanonicalFeature = Exclude<AnalysisFeature, 'metadata'>
export type AnalysisStatus = 'running' | 'completed' | 'failed' | 'canceled'

export interface CreateAnalysisRequest {
  videoId: string
  partIds: string[]
  features: AnalysisFeature[]
  language: string
  accessMode: Exclude<AccessMode, 'auto'>
  asrModel: string
  ocrResolution: 'economy' | 'balanced' | 'detail'
  reuseExisting?: boolean
}

export interface TranscriptEditSegmentRequest {
  startSeconds: number
  endSeconds: number
  text: string
}

export interface EditTranscriptRequest {
  segments: TranscriptEditSegmentRequest[]
}

export interface AnalysisFailure {
  code: string
  message: string
  action: string | null
}

export interface AnalysisContainerInfo {
  formatNames: string[]
  formatLongName: string | null
  durationSeconds: number | null
  sizeBytes: number | null
  bitRate: number | null
  startTimeSeconds: number | null
}

export interface AnalysisKeyframeStatistics {
  count: number
  timestampsSeconds: number[]
  averageIntervalSeconds: number | null
  minimumIntervalSeconds: number | null
  maximumIntervalSeconds: number | null
  truncated: boolean
}

export interface AnalysisVideoTechnicalInfo {
  index: number
  codecName: string | null
  codecLongName: string | null
  profile: string | null
  level: number | null
  width: number | null
  height: number | null
  pixelFormat: string | null
  averageFrameRate: number | null
  realFrameRate: number | null
  durationSeconds: number | null
  bitRate: number | null
  frameCount: number | null
  colorRange: string | null
  colorSpace: string | null
  colorTransfer: string | null
  colorPrimaries: string | null
  hdrType: string | null
  keyframes: AnalysisKeyframeStatistics | null
}

export interface AnalysisAudioTechnicalInfo {
  index: number
  codecName: string | null
  codecLongName: string | null
  profile: string | null
  sampleFormat: string | null
  sampleRateHz: number | null
  channels: number | null
  channelLayout: string | null
  durationSeconds: number | null
  bitRate: number | null
  bitsPerSample: number | null
}

export interface AnalysisMediaReport {
  probeName: string | null
  probeVersion: string | null
  container: AnalysisContainerInfo | null
  videoStreams: AnalysisVideoTechnicalInfo[]
  audioStreams: AnalysisAudioTechnicalInfo[]
  subtitleStreams: Array<{
    index: number
    codecName: string | null
    language: string | null
    title: string | null
  }>
  chapters: Array<{
    index: number
    startSeconds: number
    endSeconds: number
    title: string | null
  }>
  warnings: string[]
}

export interface AnalysisLoudnessPoint {
  timestampSeconds: number
  momentaryLufs: number | null
  shortTermLufs: number | null
  integratedLufs: number | null
  loudnessRangeLu: number | null
}

export interface AnalysisSilenceInterval {
  startSeconds: number
  endSeconds: number
  durationSeconds: number
}

export interface AnalysisSpectrumBand {
  key: string
  label: string
  minimumFrequencyHz: number
  maximumFrequencyHz: number
  relativeMagnitude: number
  magnitudeShare: number
  peakMagnitude: number
}

export interface AnalysisSpectrumOverview {
  analyzerName: string | null
  analyzerVersion: string | null
  frequencyScale: string
  minimumFrequencyHz: number
  maximumFrequencyHz: number
  analyzedDurationSeconds: number | null
  timeBins: number
  frequencyBins: number
  dominantFrequencyHz: number | null
  spectralCentroidHz: number | null
  bands: AnalysisSpectrumBand[]
  disclaimer: string
}

export type AnalysisAudioContentLabel =
  | 'silence'
  | 'speech_likely'
  | 'music_likely'
  | 'mixed_or_uncertain'

export interface AnalysisAudioContentSegment {
  index: number
  startSeconds: number
  endSeconds: number
  label: AnalysisAudioContentLabel
  confidence: number
  speechBandRatio: number
  spectralFlatness: number
  explanation: string
}

export interface AnalysisAudioContentClassification {
  classifierName: string | null
  classifierVersion: string | null
  heuristic: boolean
  segments: AnalysisAudioContentSegment[]
  disclaimer: string
  limitations: string[]
}

export interface AnalysisAudioReport {
  analyzerName: string | null
  analyzerVersion: string | null
  streamIndex: number
  integratedLoudnessLufs: number | null
  loudnessRangeLu: number | null
  samplePeakDbfs: number | null
  truePeakDbfs: number | null
  meanVolumeDb: number | null
  silenceThresholdDb: number | null
  minimumSilenceSeconds: number | null
  silenceIntervals: AnalysisSilenceInterval[]
  loudnessCurve: AnalysisLoudnessPoint[]
  spectrumOverview: AnalysisSpectrumOverview | null
  contentClassification: AnalysisAudioContentClassification | null
  warnings: string[]
}

export interface AnalysisTranscriptSegment {
  index: number
  startSeconds: number
  endSeconds: number
  text: string
  source: string
  language: string
  confidence: number | null
  evidenceId: string | null
}

export interface AnalysisTranscript {
  language: string
  source: string
  modelName: string | null
  modelVersion: string | null
  generatedAt: string | null
  warnings: string[]
  segments: AnalysisTranscriptSegment[]
  editProvenance: {
    sourceAnalysisId: string
    rootAnalysisId: string
    revision: number
    editedAt: string | null
    sourceUpdatedAt: string | null
    sourceTranscriptSource: string
  } | null
}

export interface AnalysisSceneSegment {
  index: number
  startSeconds: number
  endSeconds: number
  durationSeconds: number
  transitionScore: number | null
}

export interface AnalysisKeyframe {
  index: number
  timestampSeconds: number
  sceneIndex: number
  filename: string
  sizeBytes: number | null
  sha256: string | null
}

export interface AnalysisScenesReport {
  analyzerName: string | null
  analyzerVersion: string | null
  threshold: number | null
  durationSeconds: number | null
  scenes: AnalysisSceneSegment[]
  averageSceneLengthSeconds: number | null
  sceneDensityPerMinute: number | null
  truncated: boolean
  keyframes: AnalysisKeyframe[]
  warnings: string[]
}

export interface AnalysisEvidence {
  startSeconds: number | null
  endSeconds: number | null
  text: string
  source: string
  confidence: number | null
  evidenceId: string | null
  locator: string | null
  artifactId: string | null
}

export interface AnalysisSemanticCapability {
  name: string
  status: 'available' | 'limited' | 'unavailable' | string
  method: string
  message: string
}

export interface AnalysisSummaryReport {
  summary: string
  summarySentences: Array<{
    text: string
    score: number | null
    evidence: AnalysisEvidence | null
  }>
  keywords: Array<{
    keyword: string
    score: number | null
    occurrences: number
    evidence: AnalysisEvidence[]
  }>
  chapters: Array<{
    index: number
    title: string
    startSeconds: number
    endSeconds: number
    summary: string
    keywords: string[]
    evidence: AnalysisEvidence[]
  }>
  topics: Array<{
    topic: string
    score: number | null
    evidence: AnalysisEvidence[]
  }>
  entityCandidates: Array<{
    name: string
    category: string
    evidence: AnalysisEvidence | null
    limitation: string
  }>
  emotionTimeline: Array<{
    startSeconds: number
    endSeconds: number
    label: string
    score: number | null
    evidence: AnalysisEvidence | null
  }>
  visualEvidence: AnalysisEvidence[]
  semanticCapabilities: AnalysisSemanticCapability[]
  coverage: string
  modelName: string | null
  modelVersion: string | null
  generatedAt: string | null
  inputSources: string[]
  inputDetails: Record<string, unknown>
  inputDigestSha256: string | null
  parameters: Record<string, unknown>
  disclaimer: string
  warnings: string[]
}

export interface AnalysisBasicReport {
  generatedAt: string | null
  title: string
  description: string
  ownerName: string
  durationSeconds: number | null
  publishedAt: string | null
  tags: string[]
  partTitle: string
  pageNumber: number | null
  subtitleAvailability: string | null
}

export interface AnalysisResultData {
  artifactIds: string[]
  error: AnalysisFailure | null
  basic: AnalysisBasicReport | null
  media: AnalysisMediaReport | null
  audio: AnalysisAudioReport | null
  transcript: AnalysisTranscript | null
  scenes: AnalysisScenesReport | null
  summary: AnalysisSummaryReport | null
}

export interface AnalysisRecord {
  id: string
  videoId: string
  partId: string | null
  feature: AnalysisCanonicalFeature
  status: AnalysisStatus
  result: AnalysisResultData
  modelName: string | null
  modelVersion: string | null
  parameters: Record<string, unknown>
  jobId: string | null
  createdAt: string
  updatedAt: string
}

export interface AnalysisListResult {
  items: AnalysisRecord[]
  total: number
  limit: number
  offset: number
}

export interface AnalysisFilters {
  videoId?: string
  partId?: string
  feature?: AnalysisFeature
  status?: AnalysisStatus
  limit?: number
  offset?: number
}

export interface AnalysisCapability {
  feature: AnalysisCanonicalFeature
  component: string
  available: boolean
  version: string | null
  reasonCode: string | null
  message: string
  action: string | null
}

export type JobType =
  | 'download'
  | 'analysis'
  | 'package'
  | 'merge'
  | 'transcode'
  | 'media_analysis'
  | 'asr'
  | 'ocr'
  | 'scene_detection'
  | 'summary'
  | 'cleanup'

export type JobStatus =
  | 'queued'
  | 'preparing'
  | 'running'
  | 'post_processing'
  | 'paused'
  | 'completed'
  | 'canceled'
  | 'failed'

export type CompanionArtifactType = 'cover' | 'subtitle' | 'danmaku' | 'metadata'
export type CompanionOutcome = 'completed' | 'not_available' | 'failed'

export interface Job {
  id: string
  type: JobType
  status: JobStatus
  phase: string
  progress: number
  videoId: string | null
  videoTitle: string | null
  partTitle: string | null
  sourceUrl: string | null
  reused: boolean
  speedBytesPerSecond: number | null
  etaSeconds: number | null
  errorCode: string | null
  errorMessage: string | null
  retryCount: number
  cancelRequested: boolean
  createdAt: string
  startedAt: string | null
  finishedAt: string | null
  artifactIds: string[]
  companionOutcomes: Partial<Record<CompanionArtifactType, CompanionOutcome>>
  hasWarnings: boolean
}

export interface JobEvent {
  jobId: string
  status: JobStatus
  phase: string
  progress: number
  speedBytesPerSecond: number | null
  etaSeconds: number | null
  errorCode: string | null
  errorMessage: string | null
  companionOutcomes: Partial<Record<CompanionArtifactType, CompanionOutcome>>
  hasWarnings: boolean
  occurredAt: string
}

export interface JobDeleteResult {
  id: string
  deleted: boolean
  retainedArtifactCount: number
}

export interface JobBatchDeleteResult {
  results: JobDeleteResult[]
  failedIds: string[]
  deletedCount: number
}

export type ArtifactType =
  | 'video'
  | 'audio'
  | 'cover'
  | 'subtitle'
  | 'danmaku'
  | 'transcript'
  | 'keyframe'
  | 'report'
  | 'metadata'
  | 'archive'

export interface ArtifactMediaInfo {
  duration: number | null
  width: number | null
  height: number | null
  codec: string | null
  container: string | null
  analysisId: string | null
  analysisFeature: AnalysisCanonicalFeature | null
  partId: string | null
  sceneIndex: number | null
  timestampSeconds: number | null
  format: string | null
  artifactRole: string | null
  source: string | null
  editedFromAnalysisId: string | null
  editRootAnalysisId: string | null
  editRevision: number | null
}

export interface Artifact {
  id: string
  jobId: string | null
  videoId: string | null
  videoTitle: string | null
  partId: string | null
  partTitle: string | null
  sourceUrl: string | null
  jobStatus: JobStatus | null
  type: ArtifactType
  filename: string
  mimeType: string
  size: number
  checksum: string
  mediaInfo: ArtifactMediaInfo | null
  createdAt: string
  expiresAt: string | null
  retained: boolean
  protected: boolean
  retentionReason: string | null
  retainedAt: string | null
}

export interface ArtifactDeleteResult {
  id: string
  recordDeleted: boolean
  fileDeleted: boolean
  retained: boolean
}

export interface ArtifactBatchDeleteResult {
  results: ArtifactDeleteResult[]
  failedIds: string[]
  deletedCount: number
}

export interface PageResult<T> {
  items: T[]
  total: number
  page: number
  pageSize: number
}

export interface ArtifactFilters {
  query?: string
  type?: ArtifactType
  status?: JobStatus
  jobId?: string
  from?: string
  to?: string
  page?: number
  pageSize?: number
}

export interface JobFilters {
  status?: JobStatus
  type?: JobType
  activeOnly?: boolean
  page?: number
  pageSize?: number
}

export interface AppSettings {
  download: {
    defaultPreset: DownloadPreset
    concurrency: number
    retryLimit: number
    filenameTemplate: string
    defaultContainer: 'mp4' | 'mkv'
    minimumResolutionHeight: 360 | 480 | 720 | 1080 | null
  }
  storage: {
    artifactDirectory: string
    temporaryDirectory: string
    quotaBytes: number | null
    cleanupAfterDays: number | null
  }
  analysis: {
    language: string
    asrModel: string
    ocrEnabled: boolean
    device: 'auto' | 'cpu' | 'gpu'
    sampleIntervalSeconds: number
    maximumDurationSeconds: number | null
  }
  network: {
    timeoutSeconds: number
    rateLimitBytesPerSecond: number | null
    upstreamIntervalMilliseconds: number
  }
  privacy: {
    historyRetentionDays: number | null
    diagnosticsEnabled: boolean
  }
}

export interface DiskStatus {
  totalBytes: number
  usedBytes: number
  freeBytes: number
  artifactBytes: number
  temporaryBytes: number
}

export interface StorageStatus {
  totalBytes: number
  freeBytes: number
  artifactBytes: number
}

export type ComponentHealth = 'healthy' | 'degraded' | 'unavailable'

export interface HealthComponent {
  name: string
  status: ComponentHealth
  version: string | null
  message: string | null
}

export interface Diagnostics {
  applicationName: string
  applicationVersion: string
  environment: string
  startedAt: string
  status: ComponentHealth
  components: HealthComponent[]
  disk: DiskStatus
  queue: {
    queued: number
    running: number
    failedLast24Hours: number
  }
  requestId: string | null
}

export interface RecentVideo {
  id: string
  bvid: string
  title: string
  coverUrl: string
  ownerName: string
  duration: number
  parsedAt: string
  normalizedUrl: string
}

export interface VideoDeleteResult {
  id: string
  deleted: boolean
}

export interface VideoBatchDeleteResult {
  results: VideoDeleteResult[]
  failedIds: string[]
  deletedCount: number
}
