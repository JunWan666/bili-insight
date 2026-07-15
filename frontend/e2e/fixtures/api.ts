import {
  expect,
  test as base,
  type Page,
  type Request,
  type Route,
} from '@playwright/test'
import type {
  AppAuthStatus,
  AppSettings,
  Artifact,
  AuthStatus,
  Diagnostics,
  Job,
  MediaStream,
  StreamCollection,
  VideoDetail,
} from '../../src/types/api'

const apiPrefix = '/api/v1'
const fixedNow = '2026-07-14T08:00:00.000Z'

const testCover =
  'data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%22800%22 height=%22500%22 viewBox=%220 0 800 500%22%3E%3Crect width=%22800%22 height=%22500%22 fill=%22%23465acb%22/%3E%3Ccircle cx=%22400%22 cy=%22250%22 r=%22110%22 fill=%22%23eef1ff%22/%3E%3Cpath d=%22M370 185l105 65-105 65z%22 fill=%22%23465acb%22/%3E%3C/svg%3E'

interface ParseRequestRecord {
  url: string
  accessMode: string
  forceRefresh: boolean
  browserCookieHeader: string | null
}

interface ArtifactDeletionRecord {
  artifactId: string
  deleteFile: boolean
}

export interface TestApiState {
  appInitialized: boolean
  appAuthenticated: boolean
  appUsername: string
  authenticated: boolean
  premium: boolean
  remembered: boolean
  parseRequests: ParseRequestRecord[]
  streamVerificationRequests: Array<{ streamId: string; accessMode: string }>
  previewRequests: Array<Record<string, unknown>>
  previewDeletes: string[]
  downloadRequests: Array<Record<string, unknown>>
  downloadBatchRequests: Array<Array<Record<string, unknown>>>
  analysisRequests: Array<Record<string, unknown>>
  analysisEdits: Array<{ analysisId: string; body: Record<string, unknown> }>
  settingsUpdates: AppSettings[]
  artifactDeletions: ArtifactDeletionRecord[]
  artifactBatchDeletions: Array<{ artifactIds: string[]; deleteFile: boolean }>
  cookieUploadCount: number
  cookieClearCount: number
  jobListRequestCount: number
  settingsReadRequestCount: number
  analysisListRequestCount: number
  analysisQueries: Array<{ videoId: string | null; partId: string | null }>
  jobs: Job[]
  artifacts: Artifact[]
  analyses: Array<Record<string, unknown>>
  unhandledRequests: string[]
}

export interface TestApiController {
  state: TestApiState
  setAppAuthenticated(value: boolean): void
  setAppInitialized(value: boolean): void
  setAuthenticated(value: boolean): void
  setJobs(jobs: Job[]): void
}

type Fixtures = {
  testApi: TestApiController
}

function clone<T>(value: T): T {
  return structuredClone(value)
}

function appAuthStatus(state: TestApiState): AppAuthStatus {
  return {
    initialized: state.appInitialized,
    authenticated: state.appAuthenticated,
    username: state.appAuthenticated ? state.appUsername : null,
    csrfToken: state.appAuthenticated ? 'e2e-csrf-token' : null,
    sessionExpiresAt: state.appAuthenticated ? '2026-07-15T20:00:00.000Z' : null,
  }
}

function authStatus(state: TestApiState): AuthStatus {
  if (!state.authenticated) {
    return {
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
  }
  return {
    status: state.premium ? 'premium' : 'authenticated',
    isAuthenticated: true,
    isPremium: state.premium,
    maskedAccountName: '测***号',
    membershipType: state.premium ? '年度大会员' : '正式会员',
    cookieExpiresAt: '2027-07-14T08:00:00.000Z',
    lastValidatedAt: fixedNow,
    remembered: state.remembered,
    message: null,
  }
}

function videoDetail(state: TestApiState, accessModeUsed: 'anonymous' | 'authenticated' = 'anonymous'): VideoDetail {
  return {
    id: 'video-e2e',
    provider: 'bilibili',
    bvid: 'BV1FYT5zkE1q',
    aid: '100200300',
    title: 'E2E 测试专用：响应式视频解析样本',
    description: '这是仅用于端到端测试的脱敏固定数据。它用于验证长标题、分 P、媒体流、下载抽屉和分析入口，不对应任何账号凭据或签名媒体地址。',
    coverUrl: testCover,
    ownerName: '测试数据提供者',
    duration: 218,
    publishedAt: '2026-07-01T03:00:00.000Z',
    parsedAt: fixedNow,
    fromCache: false,
    accessModeUsed,
    authAvailable: state.authenticated,
    normalizedUrl: 'https://www.bilibili.com/video/BV1FYT5zkE1q?p=2',
    selectedPartId: 'part-2',
    tags: ['端到端测试', '响应式布局', '脱敏样本'],
    statistics: {
      views: 123456,
      likes: 4567,
      favorites: 890,
      danmaku: 321,
      coins: 654,
      shares: 87,
    },
    rights: {
      copyright: 'original',
      isPaid: false,
      isPremiumOnly: false,
    },
    parts: [
      {
        id: 'part-1',
        videoId: 'video-e2e',
        cid: '900001',
        pageNumber: 1,
        title: '第一部分：匿名解析',
        duration: 108,
      },
      {
        id: 'part-2',
        videoId: 'video-e2e',
        cid: '900002',
        pageNumber: 2,
        title: '第二部分：媒体选择与任务创建',
        duration: 110,
      },
    ],
  }
}

function videoStream(
  partId: string,
  id: string,
  qualityCode: string,
  qualityLabel: string,
  codec: string,
  width: number,
  height: number,
  bitrate: number,
  estimatedSize: number,
  authRequired: boolean,
  compatibilityNote: string,
): MediaStream {
  return {
    id,
    partId,
    kind: 'video',
    qualityCode,
    qualityLabel,
    codec,
    container: 'mp4',
    width,
    height,
    fps: 25,
    bitrate,
    hdrType: 'SDR',
    audioChannels: null,
    sampleRate: null,
    estimatedSize,
    authRequired,
    premiumRequired: authRequired,
    accessRequirement: authRequired ? 'premium' : 'none',
    verifiedAt: null,
    compatibleDevices: codec === 'H.264 / AVC' ? ['desktop', 'mobile', 'tv'] : ['modern-desktop', 'modern-mobile'],
    compatibilityNote,
    mimeType: 'video/mp4',
    codecString: codec === 'H.264 / AVC'
      ? 'avc1.640028'
      : codec === 'H.265 / HEVC'
        ? 'hev1.1.6.L150.90'
        : 'av01.0.08M.08',
    previewSupported: true,
  }
}

function audioStream(partId: string, id: string, bitrate: number): MediaStream {
  return {
    id,
    partId,
    kind: 'audio',
    qualityCode: String(bitrate),
    qualityLabel: `${Math.round(bitrate / 1000)} kbps`,
    codec: 'AAC',
    container: 'm4a',
    width: null,
    height: null,
    fps: null,
    bitrate,
    hdrType: null,
    audioChannels: 2,
    sampleRate: 48000,
    estimatedSize: bitrate === 192000 ? 5_300_000 : 3_600_000,
    authRequired: false,
    premiumRequired: false,
    accessRequirement: 'none',
    verifiedAt: null,
    compatibleDevices: ['desktop', 'mobile', 'tv'],
    compatibilityNote: '广泛兼容',
    mimeType: 'audio/mp4',
    codecString: 'mp4a.40.2',
    previewSupported: true,
  }
}

function streamsFor(partId: string, accessModeUsed: 'anonymous' | 'authenticated', authAvailable: boolean): StreamCollection {
  const anonymousStreams = [
    videoStream(partId, 'video-720-avc', '64', '720P 高清', 'H.264 / AVC', 1280, 720, 1_450_000, 40_200_000, false, '广泛兼容'),
    videoStream(partId, 'video-720-av1', '64', '720P 高清', 'AV1', 1280, 720, 910_000, 26_500_000, false, '需较新设备'),
  ]
  const authenticatedStreams = [
    videoStream(partId, 'video-1080-avc', '112', '1080P+ 高码率', 'H.264 / AVC', 1920, 1080, 3_550_000, 98_000_000, true, '广泛兼容'),
    videoStream(partId, 'video-1080-hevc', '112', '1080P+ 高码率', 'H.265 / HEVC', 1920, 1080, 2_070_000, 58_000_000, true, '新款设备兼容'),
    videoStream(partId, 'video-1080-av1', '112', '1080P+ 高码率', 'AV1', 1920, 1080, 1_820_000, 51_000_000, true, '需较新设备'),
    ...anonymousStreams,
  ]
  return {
    partId,
    accessModeUsed,
    authAvailable,
    parsedAt: fixedNow,
    videos: accessModeUsed === 'authenticated' ? authenticatedStreams : anonymousStreams,
    audios: [audioStream(partId, 'audio-aac-192', 192000), audioStream(partId, 'audio-aac-128', 128000)],
  }
}

function createJob(overrides: Partial<Job> = {}): Job {
  return {
    id: 'job-completed-e2e',
    type: 'download',
    status: 'completed',
    phase: 'verifying',
    progress: 100,
    videoId: 'video-e2e',
    videoTitle: 'E2E 测试专用：响应式视频解析样本',
    partTitle: '第二部分：媒体选择与任务创建',
    speedBytesPerSecond: null,
    etaSeconds: 0,
    errorCode: null,
    errorMessage: null,
    retryCount: 0,
    cancelRequested: false,
    createdAt: '2026-07-14T07:40:00.000Z',
    startedAt: '2026-07-14T07:40:01.000Z',
    finishedAt: '2026-07-14T07:41:00.000Z',
    artifactIds: ['artifact-e2e'],
    companionOutcomes: {},
    hasWarnings: false,
    ...overrides,
    sourceUrl: overrides.sourceUrl === undefined
      ? 'https://www.bilibili.com/video/BV1TEST/?p=2'
      : overrides.sourceUrl,
    reused: overrides.reused ?? false,
  }
}

export function runningJob(): Job {
  return createJob({
    id: 'job-running-e2e',
    status: 'running',
    phase: 'downloading_video',
    progress: 42,
    speedBytesPerSecond: 2_400_000,
    etaSeconds: 36,
    finishedAt: null,
    artifactIds: [],
  })
}

function artifact(): Artifact {
  return {
    id: 'artifact-e2e',
    jobId: 'job-completed-e2e',
    videoId: 'video-e2e',
    videoTitle: 'E2E 测试专用：响应式视频解析样本',
    partId: 'part-2',
    partTitle: '第二部分：媒体选择与任务创建',
    sourceUrl: 'https://www.bilibili.com/video/BV1TEST/?p=2',
    jobStatus: 'completed',
    type: 'video',
    filename: 'E2E-测试专用-第二部分.mp4',
    mimeType: 'video/mp4',
    size: 103_300_000,
    checksum: 'test-only-sha256-redacted-value',
    mediaInfo: {
      duration: 110,
      width: 1920,
      height: 1080,
      codec: 'H.264 / AVC',
      container: 'mp4',
      analysisId: null,
      analysisFeature: null,
      partId: 'part-2',
      sceneIndex: null,
      timestampSeconds: null,
      format: null,
      artifactRole: null,
      source: null,
      editedFromAnalysisId: null,
      editRootAnalysisId: null,
      editRevision: null,
    },
    createdAt: '2026-07-14T07:41:00.000Z',
    expiresAt: null,
    retained: false,
    protected: false,
    retentionReason: null,
    retainedAt: null,
  }
}

function settings(): AppSettings {
  return {
    download: {
      defaultPreset: 'best_compatibility',
      concurrency: 2,
      retryLimit: 3,
      filenameTemplate: '{title} - P{page}',
      defaultContainer: 'mkv',
      minimumResolutionHeight: 720,
    },
    storage: {
      artifactDirectory: 'artifacts',
      temporaryDirectory: 'temp',
      quotaBytes: 53_687_091_200,
      cleanupAfterDays: 30,
    },
    analysis: {
      language: 'zh-CN',
      asrModel: 'small',
      ocrEnabled: true,
      device: 'auto',
      sampleIntervalSeconds: 2,
      maximumDurationSeconds: 7200,
    },
    network: {
      timeoutSeconds: 30,
      rateLimitBytesPerSecond: null,
      upstreamIntervalMilliseconds: 500,
    },
    privacy: {
      historyRetentionDays: 90,
      diagnosticsEnabled: true,
    },
  }
}

function diagnostics(): Diagnostics {
  return {
    applicationName: 'Bili Insight API',
    applicationVersion: '1.0.0-e2e',
    environment: 'test-only',
    startedAt: '2026-07-14T06:00:00.000Z',
    status: 'degraded',
    components: [
      { name: 'FastAPI', status: 'healthy', version: '0.116', message: 'API 服务运行正常' },
      { name: 'SQLite', status: 'healthy', version: '3', message: '数据库可读写' },
      { name: 'FFmpeg / FFprobe', status: 'healthy', version: '7.1', message: '媒体工具可用' },
      { name: 'ASR 模型', status: 'unavailable', version: null, message: '可选模型尚未安装' },
    ],
    disk: {
      totalBytes: 536_870_912_000,
      usedBytes: 214_748_364_800,
      freeBytes: 322_122_547_200,
      artifactBytes: 103_300_000,
      temporaryBytes: 2_048_000,
    },
    queue: {
      queued: 0,
      running: 0,
      failedLast24Hours: 1,
    },
    requestId: 'req-e2e-redacted',
  }
}

function analysisRecord(
  feature: string,
  result: Record<string, unknown>,
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    id: `analysis-${feature}-e2e`,
    videoId: 'video-e2e',
    partId: 'part-2',
    feature,
    status: 'completed',
    result,
    modelName: null,
    modelVersion: null,
    parameters: { jobId: `job-analysis-${feature}-e2e`, language: 'zh-CN' },
    createdAt: '2026-07-14T07:20:00.000Z',
    updatedAt: '2026-07-14T07:30:00.000Z',
    ...overrides,
  }
}

function analysisFixtures(): Array<Record<string, unknown>> {
  return [
    analysisRecord('media', {
      feature: 'media',
      report: {
        probeName: 'ffprobe',
        probeVersion: '7.1',
        container: {
          formatNames: ['mov', 'mp4'],
          formatLongName: 'QuickTime / MOV',
          durationSeconds: 110,
          sizeBytes: 103_300_000,
          bitRate: 3_850_000,
          startTimeSeconds: 0,
        },
        videoStreams: [{
          index: 0,
          codecName: 'h264',
          codecLongName: 'H.264 / AVC',
          profile: 'High',
          level: 40,
          width: 1920,
          height: 1080,
          pixelFormat: 'yuv420p',
          averageFrameRate: 25,
          realFrameRate: 25,
          durationSeconds: 110,
          bitRate: 3_550_000,
          frameCount: 2750,
          colorRange: 'tv',
          colorSpace: 'bt709',
          colorTransfer: 'bt709',
          colorPrimaries: 'bt709',
          hdrType: 'SDR',
          keyframes: {
            count: 5,
            timestampsSeconds: [0, 24, 49, 73, 98],
            averageIntervalSeconds: 24.5,
            minimumIntervalSeconds: 24,
            maximumIntervalSeconds: 25,
            truncated: false,
          },
        }],
        audioStreams: [{
          index: 1,
          codecName: 'aac',
          codecLongName: 'AAC',
          profile: 'LC',
          sampleFormat: 'fltp',
          sampleRateHz: 48000,
          channels: 2,
          channelLayout: 'stereo',
          durationSeconds: 110,
          bitRate: 192000,
          bitsPerSample: 0,
        }],
        subtitleStreams: [],
        chapters: [],
        warnings: [],
      },
      artifactIds: ['artifact-analysis-media-report'],
    }, { modelName: 'ffprobe', modelVersion: '7.1' }),
    analysisRecord('audio', {
      feature: 'audio',
      report: {
        analyzerName: 'ffmpeg-ebur128',
        analyzerVersion: '7.1',
        streamIndex: 1,
        integratedLoudnessLufs: -16.4,
        loudnessRangeLu: 5.2,
        samplePeakDbfs: -1.8,
        truePeakDbfs: -1.4,
        meanVolumeDb: -18.1,
        silenceThresholdDb: -42,
        minimumSilenceSeconds: 0.7,
        silenceIntervals: [
          { startSeconds: 14, endSeconds: 16.2, durationSeconds: 2.2 },
          { startSeconds: 72.4, endSeconds: 75, durationSeconds: 2.6 },
        ],
        loudnessCurve: [
          { timestampSeconds: 0, momentaryLufs: -23, shortTermLufs: -21, integratedLufs: -23, loudnessRangeLu: 0 },
          { timestampSeconds: 15, momentaryLufs: -38, shortTermLufs: -25, integratedLufs: -19, loudnessRangeLu: 3 },
          { timestampSeconds: 30, momentaryLufs: -15, shortTermLufs: -16, integratedLufs: -17.5, loudnessRangeLu: 4 },
          { timestampSeconds: 60, momentaryLufs: -17, shortTermLufs: -16.5, integratedLufs: -16.8, loudnessRangeLu: 4.8 },
          { timestampSeconds: 90, momentaryLufs: -14.5, shortTermLufs: -15.8, integratedLufs: -16.4, loudnessRangeLu: 5.2 },
        ],
        warnings: ['音乐与重叠说话可能影响响度区段判断。'],
      },
      artifactIds: ['artifact-analysis-audio-report'],
    }, { modelName: 'ffmpeg-ebur128', modelVersion: '7.1' }),
    analysisRecord('scenes', {
      feature: 'scenes',
      sceneAnalysis: {
        analyzerName: 'scene-detect',
        analyzerVersion: '1.0',
        threshold: 0.3,
        durationSeconds: 110,
        scenes: [
          { index: 0, startSeconds: 0, endSeconds: 18, durationSeconds: 18, transitionScore: 0.74 },
          { index: 1, startSeconds: 18, endSeconds: 42, durationSeconds: 24, transitionScore: 0.81 },
          { index: 2, startSeconds: 42, endSeconds: 67, durationSeconds: 25, transitionScore: 0.69 },
          { index: 3, startSeconds: 67, endSeconds: 86, durationSeconds: 19, transitionScore: 0.77 },
          { index: 4, startSeconds: 86, endSeconds: 110, durationSeconds: 24, transitionScore: null },
        ],
        averageSceneLengthSeconds: 22,
        sceneDensityPerMinute: 2.73,
        truncated: false,
        warnings: [],
      },
      keyframeAnalysis: {
        extractorName: 'ffmpeg',
        extractorVersion: '7.1',
        artifacts: [
          { index: 0, timestampSeconds: 9, sceneIndex: 0, filename: 'scene-000.jpg', sizeBytes: 18300, sha256: 'test-only-frame-0' },
          { index: 1, timestampSeconds: 30, sceneIndex: 1, filename: 'scene-001.jpg', sizeBytes: 20100, sha256: 'test-only-frame-1' },
          { index: 2, timestampSeconds: 54.5, sceneIndex: 2, filename: 'scene-002.jpg', sizeBytes: 19700, sha256: 'test-only-frame-2' },
        ],
        warnings: [],
      },
      artifactIds: ['artifact-keyframe-0', 'artifact-keyframe-1', 'artifact-keyframe-2', 'artifact-scenes-report'],
    }, { modelName: 'scene-detect', modelVersion: '1.0' }),
    analysisRecord('basic', {
      feature: 'basic',
      generatedAt: fixedNow,
      video: {
        title: 'E2E 测试专用：响应式视频解析样本',
        description: '结构化基础概览，仅使用脱敏固定数据。',
        ownerName: '测试数据提供者',
        durationSeconds: 218,
        publishedAt: '2026-07-01T03:00:00.000Z',
        tags: ['端到端测试', '分析结果'],
      },
      part: { pageNumber: 2, title: '第二部分：媒体选择与任务创建', durationSeconds: 110 },
      subtitleAvailability: 'not_found',
      artifactIds: ['artifact-basic-report'],
    }, { modelName: 'structured-metadata', modelVersion: '1.0.0' }),
    analysisRecord('asr', {
      feature: 'asr',
      document: {
        language: 'zh-CN',
        source: 'asr',
        modelName: 'faster-whisper',
        modelVersion: '1.1',
        generatedAt: fixedNow,
        warnings: ['多人重叠说话时置信度可能下降。'],
        segments: [
          { index: 1, startSeconds: 1.2, endSeconds: 5.8, text: '欢迎查看这份固定的分析测试结果。', source: 'asr', language: 'zh-CN', confidence: 0.96, evidenceId: 'asr-evidence-1' },
          { index: 2, startSeconds: 8, endSeconds: 13.4, text: '<script>不会执行</script>，字幕内容始终作为纯文本渲染。', source: 'asr', language: 'zh-CN', confidence: 0.88, evidenceId: 'asr-evidence-2' },
          { index: 3, startSeconds: 22, endSeconds: 28.5, text: '每条转写都保留时间戳、来源与置信度。', source: 'asr', language: 'zh-CN', confidence: 0.91, evidenceId: 'asr-evidence-3' },
        ],
      },
      artifactIds: ['artifact-asr-report', 'artifact-asr-srt', 'artifact-asr-vtt', 'artifact-asr-txt', 'artifact-asr-json'],
    }, { modelName: 'faster-whisper', modelVersion: '1.1' }),
    analysisRecord('ocr', {
      error: {
        code: 'MODEL_UNAVAILABLE',
        message: 'OCR 模型未安装，本步骤未完成',
        action: '在设置页启用并安装 OCR 后单独重试；其他结果已经保留',
      },
    }, {
      id: 'analysis-ocr-failed-e2e',
      status: 'failed',
      updatedAt: '2026-07-14T07:38:00.000Z',
    }),
    analysisRecord('summary', {
      feature: 'summary',
      report: {
        summary: '该视频说明了响应式解析、分析结果展示与安全导出的核心流程。',
        summarySentences: [
          {
            text: '视频元数据提供基础定位。',
            score: 1,
            evidence: { startSeconds: null, endSeconds: null, text: '响应式视频解析样本', source: 'metadata', confidence: null, evidenceId: 'metadata-current', locator: 'video.title' },
          },
          {
            text: '分析结果会保留可定位的时间戳证据。',
            score: 0.93,
            evidence: { startSeconds: 22, endSeconds: 28.5, text: '每条转写都保留时间戳、来源与置信度。', source: 'asr', confidence: 0.91, evidenceId: 'asr-evidence-3' },
          },
        ],
        keywords: [
          { keyword: '分析结果', score: 0.9, occurrences: 3, evidence: [] },
          { keyword: '时间戳', score: 0.81, occurrences: 2, evidence: [] },
        ],
        chapters: [{
          index: 0,
          title: '结果展示与安全边界',
          startSeconds: 0,
          endSeconds: 32,
          summary: '展示转写、时间戳、置信度与纯文本安全渲染。',
          keywords: ['结果', '安全'],
          evidence: [{ startSeconds: 8, endSeconds: 13.4, text: '字幕内容始终作为纯文本渲染。', source: 'asr', confidence: 0.88, evidenceId: 'asr-evidence-2' }],
        }],
        topics: [{ topic: '分析结果', score: 0.9, evidence: [] }],
        entityCandidates: [{ name: '测试数据提供者', category: 'creator_metadata', evidence: null, limitation: '仅表示投稿者元数据，不代表画面人物。' }],
        emotionTimeline: [],
        visualEvidence: [{ startSeconds: 27, endSeconds: 27, text: '镜头 2 的关键帧', source: 'keyframe', confidence: null, evidenceId: 'keyframe-e2e', locator: 'time:27.000-27.000', artifactId: 'artifact-keyframe-e2e' }],
        semanticCapabilities: [{ name: 'entities', status: 'limited', method: 'metadata-only-no-visual-identification', message: '未识别视频内人物身份或画面对象。' }],
        coverage: 'text_and_visual_evidence',
        modelName: 'local-extractive-evidence-analyzer',
        modelVersion: '2.0.0',
        parameters: { maximumSummarySentences: 5, algorithm: 'deterministic-evidence-v2' },
        generatedAt: fixedNow,
        inputSources: ['metadata', 'asr', 'keyframe'],
        inputDetails: { metadataSnapshotCount: 1, textDocumentCount: 1, textSegmentCount: 3, keyframeEvidenceCount: 1 },
        inputDigestSha256: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        disclaimer: '自动分析结果可能存在误差，请结合时间戳证据复核。',
        warnings: [],
      },
      artifactIds: ['artifact-summary-report'],
    }, { modelName: 'local-extractive-evidence-analyzer', modelVersion: '2.0.0' }),
  ]
}

function initialState(): TestApiState {
  return {
    appInitialized: true,
    appAuthenticated: true,
    appUsername: 'e2e-admin',
    authenticated: false,
    premium: true,
    remembered: false,
    parseRequests: [],
    streamVerificationRequests: [],
    previewRequests: [],
    previewDeletes: [],
    downloadRequests: [],
    downloadBatchRequests: [],
    analysisRequests: [],
    analysisEdits: [],
    settingsUpdates: [],
    artifactDeletions: [],
    artifactBatchDeletions: [],
    cookieUploadCount: 0,
    cookieClearCount: 0,
    jobListRequestCount: 0,
    settingsReadRequestCount: 0,
    analysisListRequestCount: 0,
    analysisQueries: [],
    jobs: [
      createJob(),
      createJob({
        id: 'job-failed-e2e',
        status: 'failed',
        phase: 'merging',
        progress: 73,
        errorCode: 'MEDIA_PROCESSING_FAILED',
        errorMessage: '音视频处理失败，请查看脱敏诊断并重试',
        retryCount: 1,
        finishedAt: '2026-07-14T07:50:00.000Z',
        artifactIds: [],
      }),
    ],
    artifacts: [artifact()],
    analyses: analysisFixtures(),
    unhandledRequests: [],
  }
}

async function fulfillJson(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(body),
  })
}

function jsonBody(request: Request): Record<string, unknown> {
  const value = request.postDataJSON() as unknown
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

async function installApiRoutes(page: Page, state: TestApiState): Promise<void> {
  let appSettings = settings()

  await page.route(`**${apiPrefix}/**`, async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.slice(apiPrefix.length)
    const method = request.method()

    if (method === 'GET' && path === '/app-auth/status') {
      await fulfillJson(route, appAuthStatus(state))
      return
    }
    if (method === 'POST' && path === '/app-auth/setup') {
      const body = jsonBody(request)
      state.appInitialized = true
      state.appAuthenticated = true
      state.appUsername = typeof body.username === 'string' ? body.username : 'e2e-admin'
      await fulfillJson(route, appAuthStatus(state))
      return
    }
    if (method === 'POST' && path === '/app-auth/login') {
      const body = jsonBody(request)
      state.appAuthenticated = true
      state.appUsername = typeof body.username === 'string' ? body.username : state.appUsername
      await fulfillJson(route, appAuthStatus(state))
      return
    }
    if (method === 'POST' && path === '/app-auth/logout') {
      state.appAuthenticated = false
      await fulfillJson(route, appAuthStatus(state))
      return
    }
    if (method === 'PUT' && path === '/app-auth/password') {
      await fulfillJson(route, appAuthStatus(state))
      return
    }

    if (method === 'GET' && /^\/jobs\/[^/]+\/events$/.test(path)) {
      const jobId = path.split('/')[2] ?? ''
      const job = state.jobs.find((item) => item.id === jobId)
      const event = job
        ? {
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
            occurredAt: fixedNow,
          }
        : null
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream; charset=utf-8',
        headers: { 'Cache-Control': 'no-cache' },
        body: event ? `retry: 60000\ndata: ${JSON.stringify(event)}\n\n` : 'retry: 60000\n\n',
      })
      return
    }

    if (method === 'GET' && path === '/auth/status') {
      await fulfillJson(route, authStatus(state))
      return
    }
    if (method === 'POST' && path === '/auth/cookies') {
      state.cookieUploadCount += 1
      state.authenticated = true
      state.remembered = (request.postData() ?? '').includes('local')
      await fulfillJson(route, authStatus(state))
      return
    }
    if (method === 'POST' && path === '/auth/validate') {
      await fulfillJson(route, authStatus(state))
      return
    }
    if (method === 'DELETE' && path === '/auth/cookies') {
      state.cookieClearCount += 1
      state.authenticated = false
      state.remembered = false
      await route.fulfill({ status: 204, body: '' })
      return
    }

    if (method === 'POST' && path === '/videos/parse') {
      const body = jsonBody(request)
      const requestedMode = typeof body.accessMode === 'string' ? body.accessMode : 'auto'
      const actualMode = requestedMode === 'authenticated' && state.authenticated ? 'authenticated' : 'anonymous'
      state.parseRequests.push({
        url: typeof body.url === 'string' ? body.url : '',
        accessMode: requestedMode,
        forceRefresh: body.forceRefresh === true,
        browserCookieHeader: request.headers().cookie ?? null,
      })
      await fulfillJson(route, {
        video: videoDetail(state, actualMode),
        streams: streamsFor('part-2', actualMode, state.authenticated),
      })
      return
    }
    const streamVerificationMatch = path.match(/^\/videos\/streams\/([^/]+)\/verify$/)
    if (method === 'POST' && streamVerificationMatch) {
      const streamId = decodeURIComponent(streamVerificationMatch[1] ?? '')
      const body = jsonBody(request)
      state.streamVerificationRequests.push({
        streamId,
        accessMode: typeof body.accessMode === 'string' ? body.accessMode : '',
      })
      await fulfillJson(route, { streamId, verifiedAt: fixedNow })
      return
    }
    if (method === 'POST' && path === '/previews') {
      state.previewRequests.push(jsonBody(request))
      await fulfillJson(route, {
        id: 'preview-e2e',
        manifestUrl: `${apiPrefix}/previews/preview-e2e/manifest.mpd`,
        expiresAt: '2026-07-14T08:30:00.000Z',
        duration: 110,
        video: { streamId: 'video-1080-avc', mimeType: 'video/mp4', codecString: 'avc1.640028' },
        audio: { streamId: 'audio-aac-192', mimeType: 'audio/mp4', codecString: 'mp4a.40.2' },
      }, 201)
      return
    }
    if (method === 'GET' && path === '/previews/preview-e2e/manifest.mpd') {
      await route.fulfill({ status: 503, contentType: 'application/dash+xml', body: '' })
      return
    }
    if (method === 'DELETE' && path === '/previews/preview-e2e') {
      state.previewDeletes.push('preview-e2e')
      await route.fulfill({ status: 204, body: '' })
      return
    }
    if (method === 'GET' && path === '/videos') {
      const video = videoDetail(state)
      await fulfillJson(route, [{
        id: video.id,
        bvid: video.bvid,
        title: video.title,
        coverUrl: video.coverUrl,
        ownerName: video.ownerName,
        duration: video.duration,
        parsedAt: video.parsedAt,
        normalizedUrl: video.normalizedUrl,
      }])
      return
    }
    if (method === 'GET' && path === '/videos/video-e2e') {
      await fulfillJson(route, videoDetail(state))
      return
    }
    if (method === 'POST' && path === '/videos/video-e2e/refresh') {
      const body = jsonBody(request)
      const requestedMode = body.accessMode === 'authenticated' && state.authenticated ? 'authenticated' : 'anonymous'
      await fulfillJson(route, videoDetail(state, requestedMode))
      return
    }
    const streamsMatch = path.match(/^\/videos\/video-e2e\/parts\/([^/]+)\/streams$/)
    if (method === 'GET' && streamsMatch) {
      const partId = decodeURIComponent(streamsMatch[1] ?? 'part-2')
      const requestedMode = url.searchParams.get('accessMode') === 'authenticated' && state.authenticated ? 'authenticated' : 'anonymous'
      await fulfillJson(route, streamsFor(partId, requestedMode, state.authenticated))
      return
    }

    if (method === 'GET' && path === '/jobs') {
      state.jobListRequestCount += 1
      const requestedStatus = url.searchParams.get('status')
      const requestedType = url.searchParams.get('type')
      const activeOnly = url.searchParams.get('activeOnly') === 'true'
      const activeStatuses = new Set(['queued', 'preparing', 'running', 'post_processing', 'paused'])
      const limit = Math.max(1, Number(url.searchParams.get('limit') ?? 50))
      const offset = Math.max(0, Number(url.searchParams.get('offset') ?? 0))
      const filtered = state.jobs.filter((job) => (
        (!requestedStatus || job.status === requestedStatus)
        && (!requestedType || job.type === requestedType)
        && (!activeOnly || activeStatuses.has(job.status))
      ))
      await fulfillJson(route, { items: clone(filtered.slice(offset, offset + limit)), total: filtered.length, limit, offset })
      return
    }
    if (method === 'POST' && path === '/downloads') {
      const requestBody = jsonBody(request)
      state.downloadRequests.push(requestBody)
      const job = createJob({
        id: `job-download-${state.downloadRequests.length}`,
        status: 'queued',
        phase: 'queued',
        progress: 0,
        startedAt: null,
        finishedAt: null,
        artifactIds: [],
      })
      state.jobs.unshift(job)
      await fulfillJson(route, { job, reused: false }, 202)
      return
    }
    if (method === 'POST' && path === '/downloads/batch') {
      const requestBody = jsonBody(request)
      const downloads = Array.isArray(requestBody.downloads)
        ? requestBody.downloads.filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
        : []
      state.downloadBatchRequests.push(clone(downloads))
      const items = downloads.map((download, index) => {
        const job = createJob({
          id: `job-batch-${state.downloadBatchRequests.length}-${index + 1}`,
          status: 'queued',
          phase: 'queued',
          progress: 0,
          partTitle: `P${index + 1}`,
          startedAt: null,
          finishedAt: null,
          artifactIds: [],
        })
        state.jobs.unshift(job)
        return { job, reused: false, request: download }
      })
      await fulfillJson(route, { items, createdCount: items.length, reusedCount: 0 }, 202)
      return
    }
    if (method === 'GET' && path === '/analyses/capabilities') {
      await fulfillJson(route, {
        items: [
          { feature: 'basic', component: 'structured-metadata', available: true, version: '1.0.0', reasonCode: null, message: '基础概览可用', action: null },
          { feature: 'media', component: 'ffprobe', available: true, version: '7.1', reasonCode: null, message: '媒体探测可用', action: null },
          { feature: 'audio', component: 'ffmpeg-ebur128', available: true, version: '7.1', reasonCode: null, message: '音频分析可用', action: null },
          { feature: 'subtitles', component: 'subtitle-parser', available: true, version: '1.0', reasonCode: null, message: '字幕解析可用', action: null },
          { feature: 'asr', component: 'faster-whisper', available: true, version: '1.1', reasonCode: null, message: 'ASR 可用', action: null },
          { feature: 'ocr', component: 'paddleocr', available: false, version: null, reasonCode: 'NOT_INSTALLED', message: 'OCR 模型尚未安装', action: '在设置页安装模型后重试 OCR' },
          { feature: 'scenes', component: 'scene-detect', available: true, version: '1.0', reasonCode: null, message: '镜头分析可用', action: null },
          { feature: 'summary', component: 'local-summary', available: true, version: '2.0.0', reasonCode: null, message: '多来源证据摘要可用', action: null },
        ],
      })
      return
    }
    if (method === 'GET' && path === '/analyses') {
      state.analysisListRequestCount += 1
      const requestedFeature = url.searchParams.get('feature')
      const canonicalFeature = requestedFeature === 'metadata' ? 'basic' : requestedFeature
      const requestedStatus = url.searchParams.get('status')
      const requestedVideoId = url.searchParams.get('videoId')
      const requestedPartId = url.searchParams.get('partId')
      state.analysisQueries.push({ videoId: requestedVideoId, partId: requestedPartId })
      const limit = Math.max(1, Number(url.searchParams.get('limit') ?? 50))
      const offset = Math.max(0, Number(url.searchParams.get('offset') ?? 0))
      const filtered = state.analyses.filter((item) => (
        (!requestedVideoId || item.videoId === requestedVideoId)
        && (!requestedPartId || item.partId === requestedPartId)
        && (!canonicalFeature || item.feature === canonicalFeature)
        && (!requestedStatus || item.status === requestedStatus)
      ))
      await fulfillJson(route, { items: clone(filtered.slice(offset, offset + limit)), total: filtered.length, limit, offset })
      return
    }
    const transcriptEditMatch = path.match(/^\/analyses\/([^/]+)\/transcript$/)
    if (method === 'PATCH' && transcriptEditMatch) {
      const analysisId = decodeURIComponent(transcriptEditMatch[1] ?? '')
      const requestBody = jsonBody(request)
      state.analysisEdits.push({ analysisId, body: requestBody })
      const source = state.analyses.find((entry) => entry.id === analysisId)
      if (!source) {
        await fulfillJson(route, { code: 'RESOURCE_NOT_FOUND', message: '测试分析记录不存在' }, 404)
        return
      }
      const edited = clone(source)
      const result = edited.result as Record<string, unknown>
      const sourceDocument = result.document as Record<string, unknown>
      const requestedSegments = requestBody.segments as Array<Record<string, unknown>>
      const revision = state.analysisEdits.length
      edited.id = `analysis-asr-edited-e2e-${revision}`
      edited.modelName = 'manual-transcript-editor'
      edited.modelVersion = '1.0.0'
      edited.updatedAt = fixedNow
      edited.parameters = {
        jobId: 'job-analysis-asr-e2e',
        editedFromAnalysisId: analysisId,
        editRootAnalysisId: 'analysis-asr-e2e',
        editRevision: revision,
      }
      result.document = {
        ...sourceDocument,
        source: 'edited',
        modelName: 'manual-transcript-editor',
        modelVersion: '1.0.0',
        generatedAt: fixedNow,
        segments: requestedSegments.map((segment, index) => ({
          ...segment,
          index: index + 1,
          source: 'edited',
          language: sourceDocument.language,
          confidence: null,
          evidenceId: `edited-e2e-${revision}-${index + 1}`,
        })),
      }
      result.editProvenance = {
        sourceAnalysisId: analysisId,
        rootAnalysisId: 'analysis-asr-e2e',
        revision,
        editedAt: fixedNow,
        sourceUpdatedAt: source.updatedAt,
        sourceTranscriptSource: sourceDocument.source,
      }
      result.artifactIds = [
        `artifact-edited-report-${revision}`,
        `artifact-edited-srt-${revision}`,
        `artifact-edited-vtt-${revision}`,
        `artifact-edited-txt-${revision}`,
        `artifact-edited-json-${revision}`,
      ]
      state.analyses.unshift(edited)
      await fulfillJson(route, edited, 201)
      return
    }
    const analysisDetailMatch = path.match(/^\/analyses\/([^/]+)$/)
    if (method === 'GET' && analysisDetailMatch) {
      const analysisId = decodeURIComponent(analysisDetailMatch[1] ?? '')
      const item = state.analyses.find((entry) => entry.id === analysisId)
      await fulfillJson(route, item ?? { code: 'RESOURCE_NOT_FOUND', message: '测试分析记录不存在' }, item ? 200 : 404)
      return
    }
    if (method === 'POST' && path === '/analyses') {
      const requestBody = jsonBody(request)
      state.analysisRequests.push(requestBody)
      const job = createJob({
        id: `job-analysis-${state.analysisRequests.length}`,
        type: 'media_analysis',
        status: 'queued',
        phase: 'queued',
        progress: 0,
        startedAt: null,
        finishedAt: null,
        artifactIds: [],
      })
      state.jobs.unshift(job)
      await fulfillJson(route, job, 201)
      return
    }
    const jobActionMatch = path.match(/^\/jobs\/([^/]+)\/(cancel|retry|pause|resume)$/)
    if (method === 'POST' && jobActionMatch) {
      const jobId = decodeURIComponent(jobActionMatch[1] ?? '')
      const action = jobActionMatch[2]
      const job = state.jobs.find((item) => item.id === jobId)
      if (!job) {
        await fulfillJson(route, { code: 'JOB_NOT_FOUND', message: '测试任务不存在' }, 404)
        return
      }
      if (action === 'cancel') Object.assign(job, { status: 'canceled', phase: 'canceled', finishedAt: fixedNow })
      if (action === 'retry') Object.assign(job, { status: 'queued', phase: 'queued', progress: 0, finishedAt: null })
      if (action === 'pause') Object.assign(job, { status: 'paused', phase: 'paused' })
      if (action === 'resume') Object.assign(job, { status: 'running', phase: 'downloading_video' })
      await fulfillJson(route, clone(job))
      return
    }

    if (method === 'GET' && path === '/artifacts') {
      await fulfillJson(route, { items: clone(state.artifacts), total: state.artifacts.length, page: 1, pageSize: 20 })
      return
    }
    if (method === 'GET' && path === '/artifacts/storage') {
      await fulfillJson(route, { artifactBytes: 16_777_216, freeBytes: 80_000_000_000, totalBytes: 100_000_000_000 })
      return
    }
    const artifactContentMatch = path.match(/^\/artifacts\/([^/]+)\/content$/)
    if (method === 'GET' && artifactContentMatch) {
      await route.fulfill({
        status: 200,
        contentType: 'application/octet-stream',
        headers: { 'Accept-Ranges': 'bytes', 'Content-Disposition': 'attachment; filename="e2e-artifact.bin"' },
        body: 'test-only artifact content',
      })
      return
    }
    const artifactMatch = path.match(/^\/artifacts\/([^/]+)$/)
    if (method === 'POST' && path === '/artifacts/batch-delete') {
      const body = jsonBody(request)
      const artifactIds = Array.isArray(body.artifactIds)
        ? body.artifactIds.filter((item): item is string => typeof item === 'string')
        : []
      const deleteFile = body.deleteFile !== false
      state.artifactBatchDeletions.push({ artifactIds: clone(artifactIds), deleteFile })
      const existing = new Set(state.artifacts.map((item) => item.id))
      const deletedIds = artifactIds.filter((id) => existing.has(id))
      state.artifacts = state.artifacts.filter((item) => !deletedIds.includes(item.id))
      await fulfillJson(route, {
        results: deletedIds.map((id) => ({ id, recordDeleted: true, fileDeleted: deleteFile, retained: false })),
        failedIds: artifactIds.filter((id) => !existing.has(id)),
        deletedCount: deletedIds.length,
      })
      return
    }
    if (artifactMatch && method === 'GET') {
      const artifactId = decodeURIComponent(artifactMatch[1] ?? '')
      const item = state.artifacts.find((entry) => entry.id === artifactId)
      await fulfillJson(route, item ?? { code: 'ARTIFACT_NOT_FOUND', message: '测试产物不存在' }, item ? 200 : 404)
      return
    }
    if (artifactMatch && method === 'DELETE') {
      const artifactId = decodeURIComponent(artifactMatch[1] ?? '')
      const deleteFile = url.searchParams.get('deleteFile') === 'true'
      state.artifactDeletions.push({ artifactId, deleteFile })
      state.artifacts = state.artifacts.filter((entry) => entry.id !== artifactId)
      await route.fulfill({ status: 204, body: '' })
      return
    }

    if (method === 'GET' && path === '/settings') {
      state.settingsReadRequestCount += 1
      await fulfillJson(route, clone(appSettings))
      return
    }
    if (method === 'PUT' && path === '/settings') {
      appSettings = jsonBody(request) as unknown as AppSettings
      state.settingsUpdates.push(clone(appSettings))
      await fulfillJson(route, clone(appSettings))
      return
    }
    if (method === 'GET' && path === '/diagnostics/report') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        headers: { 'Content-Disposition': 'attachment; filename="diagnostics-test-only.json"' },
        body: JSON.stringify({ status: 'degraded', credentials: 'excluded', source: 'e2e-test-only' }),
      })
      return
    }
    if (method === 'GET' && path === '/diagnostics') {
      await fulfillJson(route, diagnostics())
      return
    }

    const requestKey = `${method} ${path}`
    state.unhandledRequests.push(requestKey)
    await fulfillJson(route, { code: 'TEST_ROUTE_MISSING', message: `缺少测试路由：${requestKey}` }, 501)
  })
}

export const test = base.extend<Fixtures>({
  testApi: [async ({ page }, use) => {
    const state = initialState()
    await installApiRoutes(page, state)
    await use({
      state,
      setAppAuthenticated(value: boolean): void {
        state.appAuthenticated = value
      },
      setAppInitialized(value: boolean): void {
        state.appInitialized = value
      },
      setAuthenticated(value: boolean): void {
        state.authenticated = value
      },
      setJobs(jobs: Job[]): void {
        state.jobs = clone(jobs)
      },
    })
    if (!page.isClosed()) {
      await page.evaluate(() => window.dispatchEvent(new Event('pagehide')))
    }
    expect(state.unhandledRequests, '所有前端 API 请求都应命中明确的测试专用 route fixture').toEqual([])
  }, { auto: true }],
})

export { expect }
