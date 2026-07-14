import { describe, expect, it } from 'vitest'
import { jobPhaseLabel } from './jobPhase'

describe('jobPhaseLabel', () => {
  it('maps every backend analysis phase to user-facing Chinese', () => {
    expect(jobPhaseLabel('analysis_preparing', '进行中')).toBe('准备分析')
    expect(jobPhaseLabel('analysis_media_acquisition', '进行中')).toBe('获取分析媒体')
    expect(jobPhaseLabel('analysis_basic', '进行中')).toBe('基础内容分析')
    expect(jobPhaseLabel('analysis_media', '进行中')).toBe('媒体技术分析')
    expect(jobPhaseLabel('analysis_audio', '进行中')).toBe('音频技术分析')
    expect(jobPhaseLabel('analysis_subtitles', '进行中')).toBe('获取公开字幕')
    expect(jobPhaseLabel('analysis_asr', '进行中')).toBe('语音转写')
    expect(jobPhaseLabel('analysis_ocr', '进行中')).toBe('画面文字识别')
    expect(jobPhaseLabel('analysis_scenes', '进行中')).toBe('镜头与关键帧分析')
    expect(jobPhaseLabel('analysis_summary', '进行中')).toBe('生成内容摘要')
    expect(jobPhaseLabel('analysis_manifest', '进行中')).toBe('生成分析清单')
  })

  it('never exposes an unknown internal phase token', () => {
    expect(jobPhaseLabel('analysis_future_internal_step', '进行中')).toBe('执行内容分析')
    expect(jobPhaseLabel('private_worker_opcode', '准备中')).toBe('准备中')
  })
})
