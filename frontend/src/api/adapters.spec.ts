import { describe, expect, it } from 'vitest'
import {
  normalizeAnalysis,
  normalizeAnalysisCapabilities,
  normalizeAnalysisList,
  normalizeArtifact,
  normalizeArtifactList,
  normalizeAuthStatus,
  normalizeDownloadBatchCreation,
  normalizeDownloadCreation,
  normalizeJob,
  normalizeJobEvent,
  normalizeJobList,
  normalizeParseResponse,
  normalizeStreams,
  normalizeStorageStatus,
} from './adapters'

describe('backend response adapters', () => {
  it('preserves whether a download creation reused an existing job', () => {
    const result = normalizeDownloadCreation({
      reused: true,
      job: { id: 'job-reused', type: 'download', status: 'completed', phase: 'completed' },
    })
    expect(result.reused).toBe(true)
    expect(result.job.id).toBe('job-reused')
    expect(result.job.status).toBe('completed')
  })

  it('preserves ordered batch creation results and reuse counts', () => {
    const result = normalizeDownloadBatchCreation({
      items: [
        { reused: false, job: { id: 'job-new', type: 'download', status: 'queued' } },
        { reused: true, job: { id: 'job-old', type: 'download', status: 'completed' } },
      ],
      createdCount: 1,
      reusedCount: 1,
    })
    expect(result.items.map((item) => [item.job.id, item.reused])).toEqual([
      ['job-new', false],
      ['job-old', true],
    ])
    expect(result).toMatchObject({ createdCount: 1, reusedCount: 1 })
  })

  it('keeps danmaku artifacts distinct from generic reports', () => {
    expect(normalizeArtifact({ id: 'dm-1', type: 'danmaku', filename: 'part.xml' }).type).toBe('danmaku')
  })

  it('maps the parse contract to the frontend domain without exposing media URLs', () => {
    const parsed = normalizeParseResponse({
      video: {
        id: 'video-1',
        provider: 'bilibili',
        bvid: 'BV1FYT5zkE1q',
        aid: 123,
        title: '测试视频',
        description: '用于验证固定契约',
        coverUrl: 'https://i.example/cover.jpg',
        ownerName: '测试 UP',
        duration: 218,
        publishedAt: '2026-07-14T00:00:00Z',
        parts: [{ id: 'part-1', videoId: 'video-1', cid: 456, pageNumber: 1, title: '第一 P', duration: 218 }],
        stats: { views: 10, likes: 2, favorites: null, danmaku: 1, coins: 1, shares: null },
        tags: ['动画'],
        rights: { copyrightCode: 1, isPaid: true, isPremiumOnly: false },
      },
      streams: {
        partId: 'part-1',
        video: [{ id: 'v1', kind: 'video', qualityCode: 112, qualityLabel: '1080P+', codec: 'H.264', container: 'mp4', width: 1920, height: 1080, fps: 25, bitrate: 3550000, hdrType: null, estimatedSize: 96800000, authRequired: true, premiumRequired: true, accessRequirement: 'premium', verifiedAt: '2026-07-14T00:01:00Z', compatibility: '广泛兼容' }],
        audio: [{ id: 'a1', kind: 'audio', qualityCode: 30280, qualityLabel: '高码率', codec: 'AAC', container: 'm4a', bitrate: 188000, sampleRate: 48000, audioChannels: 2, estimatedSize: 5100000, authRequired: false, compatibility: '广泛兼容' }],
        fetchedAt: '2026-07-14T00:01:00Z',
      },
      normalizedUrl: 'https://www.bilibili.com/video/BV1FYT5zkE1q',
      selectedPartId: 'part-1',
      sourceTime: '2026-07-14T00:01:00Z',
      cacheHit: false,
      access: { requestedMode: 'authenticated', actualMode: 'authenticated', hasCredentials: true, usedAuthentication: true, membershipType: 'premium' },
    })

    expect(parsed.video).toMatchObject({
      id: 'video-1',
      bvid: 'BV1FYT5zkE1q',
      accessModeUsed: 'authenticated',
      authAvailable: true,
      selectedPartId: 'part-1',
      rights: { copyright: '原创', isPaid: true, isPremiumOnly: false },
    })
    expect(parsed.streams?.videos[0]).toMatchObject({
      id: 'v1',
      codec: 'H.264',
      width: 1920,
      authRequired: true,
      premiumRequired: true,
      accessRequirement: 'premium',
      compatibilityNote: '广泛兼容',
    })
    expect(JSON.stringify(parsed)).not.toContain('baseUrl')
  })

  it('uses the requested stream identity only as a fallback and trusts an explicit backend access result', () => {
    const legacy = normalizeStreams(
      { partId: 'part-auth', video: [], audio: [] },
      { actualMode: 'authenticated', hasCredentials: true },
    )
    const explicitDowngrade = normalizeStreams(
      {
        partId: 'part-auth',
        video: [],
        audio: [],
        access: { actualMode: 'anonymous', hasCredentials: false, usedAuthentication: false },
      },
      { actualMode: 'authenticated', hasCredentials: true },
    )

    expect(legacy).toMatchObject({ accessModeUsed: 'authenticated', authAvailable: true })
    expect(explicitDowngrade).toMatchObject({ accessModeUsed: 'anonymous', authAvailable: false })
  })

  it('derives premium authentication state from the status contract', () => {
    expect(normalizeAuthStatus({
      status: 'valid', loggedIn: true, premium: true, membershipType: 'annual_premium',
      maskedAccountName: '测***户', persistence: 'local', hasCredentials: true,
    })).toMatchObject({ status: 'premium', isAuthenticated: true, isPremium: true, remembered: true })
  })

  it('never invents a logged-in state for invalid credentials', () => {
    expect(normalizeAuthStatus({ status: 'invalid', loggedIn: false, premium: false, hasCredentials: false })).toMatchObject({
      status: 'expired', isAuthenticated: false, isPremium: false,
    })
  })

  it('normalizes the persistent backend job shape and download wrapper', () => {
    const job = normalizeJob({
      reused: false,
      job: {
        id: 'job-1',
        type: 'download',
        status: 'running',
        phase: 'downloading_video',
        progress: 42,
        input: {
          videoId: 'video-1',
          videoTitle: '契约测试视频',
          partTitle: '第一 P',
        },
        runtime: { speedBytesPerSecond: 2048, etaSeconds: 12 },
        artifacts: [{ id: 'artifact-1' }],
        companionOutcomes: { subtitle: 'failed', cover: 'not_available', unsafe: 'failed' },
        hasWarnings: true,
        retryCount: 1,
        cancelRequested: false,
        createdAt: '2026-07-14T00:00:00Z',
      },
    })

    expect(job).toMatchObject({
      id: 'job-1',
      videoId: 'video-1',
      videoTitle: '契约测试视频',
      speedBytesPerSecond: 2048,
      etaSeconds: 12,
      artifactIds: ['artifact-1'],
      companionOutcomes: { subtitle: 'failed', cover: 'not_available' },
      hasWarnings: true,
    })
  })

  it('maps limit-offset lists and named SSE payloads to the UI domain', () => {
    const result = normalizeJobList({
      items: [{ id: 'job-2', type: 'analysis', status: 'queued', createdAt: '2026-07-14T00:00:00Z' }],
      total: 7,
      limit: 2,
      offset: 4,
    }, 1, 20)
    const event = normalizeJobEvent({
      eventId: 'job-2:3',
      event: 'progress',
      emittedAt: '2026-07-14T00:01:00Z',
      job: {
        id: 'job-2',
        type: 'analysis',
        status: 'running',
        phase: 'analyzing',
        progress: 25,
        runtime: { speedBytesPerSecond: null, etaSeconds: 9 },
        createdAt: '2026-07-14T00:00:00Z',
      },
    })

    expect(result).toMatchObject({ total: 7, page: 3, pageSize: 2 })
    expect(result.items[0]?.type).toBe('analysis')
    expect(event).toMatchObject({
      jobId: 'job-2',
      status: 'running',
      phase: 'analyzing',
      progress: 25,
      etaSeconds: 9,
    })
  })

  it('normalizes artifact pagination and safe display metadata', () => {
    const result = normalizeArtifactList({
      items: [{
        id: 'artifact-1',
        jobId: 'job-1',
        type: 'video',
        filename: 'result.mp4',
        mimeType: 'video/mp4',
        size: 1024,
        checksum: 'abc',
        mediaInfo: {
          duration: 8,
          width: 1920,
          height: 1080,
          codec: 'h264',
          container: 'mp4',
          analysis_feature: 'scenes',
          analysis_id: 'analysis-1',
          part_id: 'part-1',
          scene_index: 2,
          timestamp_seconds: 4.5,
          format: 'jpg',
          artifact_role: 'keyframe',
        },
        videoId: 'video-1',
        videoTitle: '契约测试视频',
        partId: 'part-1',
        partTitle: '第一 P',
        jobStatus: 'failed',
        createdAt: '2026-07-14T00:00:00Z',
      }],
      total: 1,
      limit: 50,
      offset: 0,
    }, 1, 50)

    expect(result.items[0]).toMatchObject({
      id: 'artifact-1',
      videoId: 'video-1',
      videoTitle: '契约测试视频',
      partId: 'part-1',
      partTitle: '第一 P',
      jobStatus: 'failed',
      mediaInfo: {
        duration: 8,
        codec: 'h264',
        analysisFeature: 'scenes',
        analysisId: 'analysis-1',
        partId: 'part-1',
        sceneIndex: 2,
        timestampSeconds: 4.5,
        format: 'jpg',
        artifactRole: 'keyframe',
      },
    })
  })

  it('normalizes the diagnostics-independent artifact storage contract', () => {
    expect(normalizeStorageStatus({ artifact_bytes: 1024, free_bytes: 2048, total_bytes: 4096 })).toEqual({
      artifactBytes: 1024,
      freeBytes: 2048,
      totalBytes: 4096,
    })
  })

  it('maps legacy dynamic analysis artifact names to the stable product categories', () => {
    expect(normalizeArtifact({ id: 'a1', type: 'analysis_scenes_keyframe_001' }).type).toBe('keyframe')
    expect(normalizeArtifact({ id: 'a2', type: 'analysis_asr_vtt' }).type).toBe('transcript')
    expect(normalizeArtifact({ id: 'a3', type: 'analysis_manifest' }).type).toBe('report')
  })

  it('normalizes managed retained files without recreating deleted privacy metadata', () => {
    const retained = normalizeArtifact({
      id: 'retained-1',
      jobId: null,
      videoTitle: null,
      mediaInfo: null,
      type: 'video',
      filename: 'kept.mp4',
      retained: true,
      protected: true,
      retentionReason: 'user_retained',
      retainedAt: '2026-07-14T01:00:00Z',
    })
    expect(retained).toMatchObject({
      id: 'retained-1',
      jobId: null,
      videoTitle: null,
      mediaInfo: null,
      retained: true,
      protected: true,
      retentionReason: 'user_retained',
      retainedAt: '2026-07-14T01:00:00Z',
    })
  })

  it('normalizes mixed snake-case media, audio and scene reports into stable analysis fields', () => {
    const result = normalizeAnalysisList({
      items: [
        {
          id: 'analysis-media',
          video_id: 'video-1',
          part_id: 'part-1',
          feature: 'media',
          status: 'completed',
          result: {
            report: {
              probe_name: 'ffprobe',
              probe_version: '7.1',
              container: {
                format_names: ['mov', 'mp4'],
                duration_seconds: 12.5,
                size_bytes: 4096,
                bit_rate: 2_000_000,
              },
              video_streams: [{
                index: 0,
                codec_name: 'h264',
                codec_long_name: 'H.264 / AVC',
                profile: 'High',
                level: 40,
                width: 1920,
                height: 1080,
                pixel_format: 'yuv420p',
                average_frame_rate: 25,
                bit_rate: 1_800_000,
                hdr_type: 'SDR',
                keyframes: {
                  count: 3,
                  timestamps_seconds: [0, 5, 10],
                  average_interval_seconds: 5,
                  truncated: false,
                },
              }],
              audio_streams: [{ index: 1, codec_name: 'aac', sample_rate_hz: 48000, channels: 2 }],
              warnings: ['固定测试提示'],
            },
            artifact_ids: ['report-media'],
          },
          model_name: 'ffprobe',
          model_version: '7.1',
          parameters: { job_id: 'job-analysis' },
          created_at: '2026-07-14T00:00:00Z',
          updated_at: '2026-07-14T00:01:00Z',
        },
        {
          id: 'analysis-audio', videoId: 'video-1', partId: 'part-1', feature: 'audio', status: 'completed',
          result: {
            report: {
              integratedLoudnessLufs: -16.2,
              loudnessRangeLu: 5.1,
              truePeakDbfs: -1.3,
              silenceIntervals: [{ startSeconds: 3, endSeconds: 4.5, durationSeconds: 1.5 }],
              loudnessCurve: [{ timestampSeconds: 1, momentaryLufs: -18, shortTermLufs: -17 }],
              spectrum_overview: {
                analyzer_name: 'ffmpeg-showspectrumpic-relative',
                minimum_frequency_hz: 20,
                maximum_frequency_hz: 20000,
                dominant_frequency_hz: 440,
                spectral_centroid_hz: 880,
                time_bins: 512,
                frequency_bins: 192,
                bands: [{ key: 'mid', label: '中频', minimum_frequency_hz: 500, maximum_frequency_hz: 2000, relative_magnitude: 1, magnitude_share: 0.6, peak_magnitude: 0.9 }],
                disclaimer: '仅为相对频谱',
              },
              content_classification: {
                classifier_name: 'bounded-spectrum-heuristic',
                heuristic: true,
                disclaimer: '不用于版权识别',
                limitations: ['歌声可能混淆'],
                segments: [{ index: 0, start_seconds: 0, end_seconds: 3, label: 'speech_likely', confidence: 0.7, speech_band_ratio: 0.8, spectral_flatness: 0.2, explanation: '更接近语音' }],
              },
            },
          },
          parameters: {}, createdAt: '2026-07-14T00:00:00Z', updatedAt: '2026-07-14T00:01:00Z',
        },
        {
          id: 'analysis-scenes', videoId: 'video-1', partId: 'part-1', feature: 'scenes', status: 'completed',
          result: {
            sceneAnalysis: {
              durationSeconds: 12.5,
              scenes: [{ index: 0, startSeconds: 0, endSeconds: 4, durationSeconds: 4, transitionScore: 0.8 }],
              averageSceneLengthSeconds: 4,
              sceneDensityPerMinute: 15,
            },
            keyframeAnalysis: {
              artifacts: [{ index: 0, timestampSeconds: 2, sceneIndex: 0, filename: 'frame-001.jpg', sizeBytes: 100 }],
            },
          },
          parameters: {}, createdAt: '2026-07-14T00:00:00Z', updatedAt: '2026-07-14T00:01:00Z',
        },
      ],
      total: 3,
      limit: 200,
      offset: 0,
    })

    expect(result.items[0]).toMatchObject({
      feature: 'media',
      jobId: 'job-analysis',
      result: {
        artifactIds: ['report-media'],
        media: {
          probeName: 'ffprobe',
          container: { formatNames: ['mov', 'mp4'], durationSeconds: 12.5 },
          videoStreams: [{ codecName: 'h264', width: 1920, keyframes: { timestampsSeconds: [0, 5, 10] } }],
          audioStreams: [{ codecName: 'aac', sampleRateHz: 48000 }],
        },
      },
    })
    expect(result.items[1]?.result.audio).toMatchObject({
      integratedLoudnessLufs: -16.2,
      silenceIntervals: [{ startSeconds: 3, endSeconds: 4.5 }],
      loudnessCurve: [{ timestampSeconds: 1, momentaryLufs: -18 }],
      spectrumOverview: {
        dominantFrequencyHz: 440,
        bands: [{ key: 'mid', relativeMagnitude: 1 }],
      },
      contentClassification: {
        heuristic: true,
        segments: [{ label: 'speech_likely', confidence: 0.7 }],
      },
    })
    expect(result.items[2]?.result.scenes).toMatchObject({
      averageSceneLengthSeconds: 4,
      sceneDensityPerMinute: 15,
      keyframes: [{ timestampSeconds: 2, filename: 'frame-001.jpg' }],
    })
  })

  it('keeps transcript evidence, summary provenance and per-step failures independently visible', () => {
    const transcript = normalizeAnalysis({
      id: 'analysis-asr', videoId: 'video-1', partId: 'part-1', feature: 'asr', status: 'completed',
      result: {
        document: {
          language: 'zh-CN', source: 'asr', modelName: 'faster-whisper', modelVersion: '1.1',
          generatedAt: '2026-07-14T00:01:00Z',
          segments: [{ index: 1, startSeconds: 1.25, endSeconds: 3.5, text: '<script>不执行</script> 安全文本', confidence: 1.4, evidenceId: 'ev-1' }],
        },
        editProvenance: {
          sourceAnalysisId: 'analysis-source', rootAnalysisId: 'analysis-root', revision: 2,
          editedAt: '2026-07-14T00:01:00Z', sourceTranscriptSource: 'asr',
        },
        artifactIds: ['asr-report', 'asr-srt'],
      },
      parameters: {}, createdAt: '2026-07-14T00:00:00Z', updatedAt: '2026-07-14T00:01:00Z',
    })
    const summary = normalizeAnalysis({
      id: 'analysis-summary', videoId: 'video-1', partId: 'part-1', feature: 'summary', status: 'completed',
      result: {
        report: {
          summary: '结构化摘要', modelName: 'local-summary', modelVersion: '1.0', generatedAt: '2026-07-14T00:02:00Z',
          disclaimer: '自动结果可能存在误差', inputSources: ['asr'],
          coverage: 'text_and_visual_evidence', inputDigestSha256: 'a'.repeat(64),
          inputDetails: { textSegmentCount: 1, keyframeEvidenceCount: 1 },
          parameters: { algorithm: 'deterministic-evidence-v2' },
          summarySentences: [
            { text: '元数据结论', score: 1, evidence: { startSeconds: null, endSeconds: null, text: '标题', source: 'metadata', locator: 'video.title' } },
            { text: '关键结论', score: 0.9, evidence: { startSeconds: 1, endSeconds: 3, text: '证据原文', source: 'asr', confidence: 0.88 } },
          ],
          keywords: [{ keyword: '测试', score: 0.7, occurrences: 2, evidence: [] }],
          chapters: [{ index: 0, title: '开场', startSeconds: 0, endSeconds: 8, summary: '章节摘要', keywords: ['测试'], evidence: [] }],
          topics: [{ topic: '测试', score: 0.7, evidence: [] }],
          entityCandidates: [{ name: '测试 UP', category: 'creator_metadata', evidence: { source: 'metadata', text: '测试 UP', locator: 'video.ownerName' }, limitation: '不代表画面人物' }],
          emotionTimeline: [],
          visualEvidence: [{ startSeconds: 2, endSeconds: 2, source: 'keyframe', text: '关键帧', artifactId: 'frame-1' }],
          semanticCapabilities: [{ name: 'entities', status: 'limited', method: 'metadata-only', message: '未识别画面人物' }],
        },
      },
      parameters: {}, createdAt: '2026-07-14T00:00:00Z', updatedAt: '2026-07-14T00:02:00Z',
    })
    const failed = normalizeAnalysis({
      id: 'analysis-ocr-failed', videoId: 'video-1', partId: 'part-1', feature: 'ocr', status: 'failed',
      result: { error: { code: 'MODEL_UNAVAILABLE', message: 'OCR 模型不可用', action: '安装模型后单独重试' } },
      parameters: {}, createdAt: '2026-07-14T00:00:00Z', updatedAt: '2026-07-14T00:03:00Z',
    })

    expect(transcript.result.transcript?.segments[0]).toMatchObject({
      text: '<script>不执行</script> 安全文本', confidence: 1, evidenceId: 'ev-1',
    })
    expect(transcript.result.transcript?.editProvenance).toMatchObject({ revision: 2, sourceTranscriptSource: 'asr' })
    expect(summary.result.summary).toMatchObject({
      summary: '结构化摘要',
      inputSources: ['asr'],
      coverage: 'text_and_visual_evidence',
      inputDigestSha256: 'a'.repeat(64),
      inputDetails: { textSegmentCount: 1, keyframeEvidenceCount: 1 },
      summarySentences: [
        { evidence: { startSeconds: null, locator: 'video.title', source: 'metadata' } },
        { evidence: { startSeconds: 1, text: '证据原文', confidence: 0.88 } },
      ],
      chapters: [{ title: '开场', keywords: ['测试'] }],
      topics: [{ topic: '测试' }],
      entityCandidates: [{ name: '测试 UP', limitation: '不代表画面人物' }],
      visualEvidence: [{ artifactId: 'frame-1', source: 'keyframe' }],
      semanticCapabilities: [{ name: 'entities', status: 'limited' }],
    })
    expect(failed.result.error).toEqual({ code: 'MODEL_UNAVAILABLE', message: 'OCR 模型不可用', action: '安装模型后单独重试' })
    expect(failed.result.transcript).toBeNull()
  })

  it('canonicalizes metadata capabilities and preserves actionable availability messages', () => {
    expect(normalizeAnalysisCapabilities({ items: [
      { feature: 'metadata', component: 'structured-metadata', available: true, version: '1.0', message: '可用' },
      { feature: 'ocr', component: 'paddleocr', available: false, reason_code: 'NOT_INSTALLED', message: 'OCR 模型未安装', action: '在设置页安装模型' },
    ] })).toEqual([
      { feature: 'basic', component: 'structured-metadata', available: true, version: '1.0', reasonCode: null, message: '可用', action: null },
      { feature: 'ocr', component: 'paddleocr', available: false, version: null, reasonCode: 'NOT_INSTALLED', message: 'OCR 模型未安装', action: '在设置页安装模型' },
    ])
  })
})
