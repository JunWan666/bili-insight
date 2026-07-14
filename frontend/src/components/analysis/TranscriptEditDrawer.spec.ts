import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'
import TranscriptEditDrawer from './TranscriptEditDrawer.vue'
import type { AnalysisTranscript } from '@/types/api'

function transcript(overrides: Partial<AnalysisTranscript> = {}): AnalysisTranscript {
  return {
    language: 'zh-CN',
    source: 'asr',
    modelName: 'faster-whisper',
    modelVersion: '1.1',
    generatedAt: '2026-07-14T00:00:00Z',
    warnings: [],
    editProvenance: null,
    segments: [{
      index: 1,
      startSeconds: 0,
      endSeconds: 2,
      text: '<script>不会执行</script> 只是字幕文本',
      source: 'asr',
      language: 'zh-CN',
      confidence: 0.9,
      evidenceId: 'asr-1',
    }],
    ...overrides,
  }
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('TranscriptEditDrawer', () => {
  it('keeps markup-looking subtitle content as plain editable text and emits a full replacement', async () => {
    const wrapper = mount(TranscriptEditDrawer, {
      attachTo: document.body,
      props: {
        modelValue: true,
        transcript: transcript(),
        saving: false,
        errorMessage: null,
      },
    })
    await nextTick()

    const textarea = document.body.querySelector('textarea') as HTMLTextAreaElement | null
    expect(textarea?.value).toContain('<script>不会执行</script>')
    const save = document.body.querySelector('[data-testid="save-transcript-edit"]') as HTMLButtonElement
    expect(save.disabled).toBe(false)
    save.click()
    await nextTick()

    expect(wrapper.emitted('save')?.[0]?.[0]).toEqual({
      segments: [{
        startSeconds: 0,
        endSeconds: 2,
        text: '<script>不会执行</script> 只是字幕文本',
      }],
    })
    wrapper.unmount()
  })

  it('blocks invalid timestamps before sending a PATCH request', async () => {
    const value = transcript()
    value.segments[0]!.startSeconds = 5
    value.segments[0]!.endSeconds = 2
    const wrapper = mount(TranscriptEditDrawer, {
      attachTo: document.body,
      props: {
        modelValue: true,
        transcript: value,
        saving: false,
        errorMessage: null,
      },
    })
    await nextTick()

    expect(document.body.textContent).toContain('结束时间必须晚于开始时间')
    const save = document.body.querySelector('[data-testid="save-transcript-edit"]') as HTMLButtonElement
    expect(save.disabled).toBe(true)
    save.click()
    expect(wrapper.emitted('save')).toBeUndefined()
    wrapper.unmount()
  })

  it('rejects decreasing start times while allowing overlapping segments', async () => {
    const baseSegments = transcript().segments
    const overlapping = [
      { ...baseSegments[0]!, endSeconds: 3 },
      {
        ...baseSegments[0]!,
        index: 2,
        startSeconds: 2,
        endSeconds: 4,
        text: '允许重叠的第二段',
        evidenceId: 'asr-2',
      },
    ]
    const wrapper = mount(TranscriptEditDrawer, {
      attachTo: document.body,
      props: {
        modelValue: true,
        transcript: transcript({ segments: overlapping }),
        saving: false,
        errorMessage: null,
      },
    })
    await nextTick()

    const save = document.body.querySelector('[data-testid="save-transcript-edit"]') as HTMLButtonElement
    expect(save.disabled).toBe(false)

    await wrapper.setProps({
      modelValue: false,
      transcript: transcript({
        segments: [
          { ...overlapping[0]!, startSeconds: 2 },
          { ...overlapping[1]!, startSeconds: 1 },
        ],
      }),
    })
    await wrapper.setProps({ modelValue: true })
    await nextTick()

    expect(document.body.textContent).toContain('开始时间不能早于上一段')
    expect(save.disabled).toBe(true)
    save.click()
    expect(wrapper.emitted('save')).toBeUndefined()
    wrapper.unmount()
  })
})
