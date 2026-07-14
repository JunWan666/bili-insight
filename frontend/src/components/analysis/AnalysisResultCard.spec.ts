import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import AnalysisResultCard from './AnalysisResultCard.vue'
import type { AnalysisRecord } from '@/types/api'

const record = {
  id: 'analysis-audio',
  videoId: 'video-1',
  partId: 'part-1',
  feature: 'audio',
  status: 'completed',
  result: {
    artifactIds: [],
    error: null,
    basic: null,
    media: null,
    transcript: null,
    scenes: null,
    summary: null,
    audio: {
      analyzerName: 'ffmpeg-ebur128',
      analyzerVersion: '7.1',
      streamIndex: 0,
      integratedLoudnessLufs: -16,
      loudnessRangeLu: 4,
      samplePeakDbfs: -1,
      truePeakDbfs: -0.8,
      meanVolumeDb: -18,
      silenceThresholdDb: -50,
      minimumSilenceSeconds: 0.5,
      silenceIntervals: [],
      loudnessCurve: [],
      spectrumOverview: {
        analyzerName: 'ffmpeg-showspectrumpic-relative',
        analyzerVersion: '7.1',
        frequencyScale: 'logarithmic',
        minimumFrequencyHz: 20,
        maximumFrequencyHz: 20_000,
        analyzedDurationSeconds: 10,
        timeBins: 512,
        frequencyBins: 192,
        dominantFrequencyHz: 440,
        spectralCentroidHz: 880,
        bands: [{ key: 'mid', label: '中频', minimumFrequencyHz: 500, maximumFrequencyHz: 2_000, relativeMagnitude: 1, magnitudeShare: 0.7, peakMagnitude: 0.9 }],
        disclaimer: '这是相对频谱，不是音频指纹。',
      },
      contentClassification: {
        classifierName: 'bounded-spectrum-heuristic',
        classifierVersion: '1.0.0',
        heuristic: true,
        segments: [{ index: 0, startSeconds: 0, endSeconds: 10, label: 'speech_likely', confidence: 0.7, speechBandRatio: 0.8, spectralFlatness: 0.2, explanation: '特征更接近语音，但歌声可能混淆。' }],
        disclaimer: '仅为启发式，不用于版权识别。',
        limitations: ['不执行精确音乐或说话人识别。'],
      },
      warnings: [],
    },
  },
  modelName: 'ffmpeg-ebur128',
  modelVersion: '7.1',
  parameters: {},
  jobId: 'job-1',
  createdAt: '2026-07-14T00:00:00Z',
  updatedAt: '2026-07-14T00:01:00Z',
} as AnalysisRecord

describe('AnalysisResultCard audio evidence', () => {
  it('renders relative spectrum and honest heuristic classification limitations', () => {
    const wrapper = mount(AnalysisResultCard, {
      props: { record },
      global: {
        stubs: {
          AnalysisTimelineChart: true,
          SpectrumOverviewChart: true,
          RouterLink: true,
        },
      },
    })
    expect(wrapper.text()).toContain('音频响度、频谱与区段')
    expect(wrapper.text()).toContain('频谱概览')
    expect(wrapper.text()).toContain('不是精确内容或版权识别')
    expect(wrapper.text()).toContain('可能语音')
    expect(wrapper.text()).toContain('置信度 70%')
    expect(wrapper.text()).toContain('不执行精确音乐或说话人识别')
  })

  it('renders markup-looking transcript content as escaped text and exposes editing provenance', () => {
    const transcriptRecord = {
      ...record,
      id: 'analysis-asr-edited',
      feature: 'asr',
      result: {
        ...record.result,
        audio: null,
        transcript: {
          language: 'zh-CN',
          source: 'edited',
          modelName: 'manual-transcript-editor',
          modelVersion: '1.0.0',
          generatedAt: '2026-07-14T00:02:00Z',
          warnings: [],
          editProvenance: {
            sourceAnalysisId: 'source-analysis',
            rootAnalysisId: 'root-analysis',
            revision: 2,
            editedAt: '2026-07-14T00:02:00Z',
            sourceUpdatedAt: '2026-07-14T00:01:00Z',
            sourceTranscriptSource: 'asr',
          },
          segments: [{
            index: 1,
            startSeconds: 0,
            endSeconds: 2,
            text: '<script>不会执行</script> 只是字幕文本',
            source: 'edited',
            language: 'zh-CN',
            confidence: null,
            evidenceId: 'edited-1',
          }],
        },
      },
      modelName: 'manual-transcript-editor',
      modelVersion: '1.0.0',
    } as AnalysisRecord
    const wrapper = mount(AnalysisResultCard, {
      props: { record: transcriptRecord },
      global: { stubs: { TranscriptEditDrawer: true, RouterLink: true } },
    })

    expect(wrapper.text()).toContain('<script>不会执行</script> 只是字幕文本')
    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.html()).toContain('&lt;script&gt;不会执行&lt;/script&gt;')
    expect(wrapper.text()).toContain('人工编辑修订 #2')
    expect(wrapper.get('[data-testid="open-transcript-editor"]').text()).toContain('编辑与重新导出')
  })

  it('shows multi-source summary coverage, evidence locators and honest semantic limits', () => {
    const summaryRecord = {
      ...record,
      id: 'analysis-summary',
      feature: 'summary',
      result: {
        ...record.result,
        audio: null,
        summary: {
          summary: '元数据与时间轴文本共同支持该概览。',
          summarySentences: [{
            text: '视频标题提供基础定位。',
            score: 1,
            evidence: {
              startSeconds: null,
              endSeconds: null,
              text: '标题字段',
              source: 'metadata',
              confidence: null,
              evidenceId: 'metadata-1',
              locator: 'video.title',
              artifactId: null,
            },
          }],
          keywords: [{ keyword: '测试', score: 1, occurrences: 2, evidence: [] }],
          chapters: [],
          topics: [{ topic: '测试', score: 1, evidence: [] }],
          entityCandidates: [{
            name: '测试 UP',
            category: 'creator_metadata',
            evidence: null,
            limitation: '只表示投稿者，不代表画面人物。',
          }],
          emotionTimeline: [],
          visualEvidence: [{
            startSeconds: 2,
            endSeconds: 2,
            text: '关键帧定位',
            source: 'keyframe',
            confidence: null,
            evidenceId: 'frame-1',
            locator: 'time:2.000-2.000',
            artifactId: 'artifact-frame',
          }],
          semanticCapabilities: [{
            name: 'entities',
            status: 'limited',
            method: 'metadata-only',
            message: '未识别视频内人物或对象。',
          }],
          coverage: 'text_and_visual_evidence',
          modelName: 'local-extractive-evidence-analyzer',
          modelVersion: '2.0.0',
          generatedAt: '2026-07-14T00:03:00Z',
          inputSources: ['metadata', 'asr', 'keyframe'],
          inputDetails: { textSegmentCount: 1, keyframeEvidenceCount: 1 },
          inputDigestSha256: 'a'.repeat(64),
          parameters: { algorithm: 'deterministic-evidence-v2' },
          disclaimer: '自动分析结果可能存在误差。',
          warnings: [],
        },
      },
      modelName: 'local-extractive-evidence-analyzer',
      modelVersion: '2.0.0',
    } as AnalysisRecord
    const wrapper = mount(AnalysisResultCard, {
      props: { record: summaryRecord },
      global: { stubs: { TranscriptEditDrawer: true, RouterLink: true } },
    })

    expect(wrapper.text()).toContain('元数据 + 时间轴文本 + 结构化画面证据')
    expect(wrapper.text()).toContain('deterministic-evidence-v2')
    expect(wrapper.text()).toContain('video.title · 元数据')
    expect(wrapper.text()).toContain('确定性主题候选')
    expect(wrapper.text()).toContain('只表示投稿者，不代表画面人物')
    expect(wrapper.text()).toContain('未识别视频内人物或对象')
    expect(wrapper.find(`a[href$="/artifacts/artifact-frame/content"]`).exists()).toBe(true)
  })
})
