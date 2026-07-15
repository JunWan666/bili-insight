import { mount } from '@vue/test-utils'
import { ElMessage } from 'element-plus'
import { describe, expect, it, vi } from 'vitest'
import StreamSelector from './StreamSelector.vue'
import type { StreamCollection } from '@/types/api'

const streams: StreamCollection = {
  partId: 'part-1', accessModeUsed: 'authenticated', authAvailable: true, parsedAt: '2026-07-14T00:00:00Z',
  videos: [
    { id: 'av1', partId: 'part-1', kind: 'video', qualityCode: '112', qualityLabel: '1080P+', codec: 'AV1', container: 'mp4', width: 1920, height: 1080, fps: 25, bitrate: 1_820_000, hdrType: null, audioChannels: null, sampleRate: null, estimatedSize: 49_000_000, authRequired: true, premiumRequired: true, accessRequirement: 'premium', verifiedAt: '2026-07-14T00:00:00Z', compatibleDevices: [], compatibilityNote: '需较新设备' },
    { id: 'h264', partId: 'part-1', kind: 'video', qualityCode: '112', qualityLabel: '1080P+', codec: 'H.264', container: 'mp4', width: 1920, height: 1080, fps: 25, bitrate: 3_550_000, hdrType: null, audioChannels: null, sampleRate: null, estimatedSize: 96_000_000, authRequired: true, premiumRequired: true, accessRequirement: 'premium', verifiedAt: '2026-07-14T00:00:00Z', compatibleDevices: [], compatibilityNote: '广泛兼容' },
    { id: 'small-480', partId: 'part-1', kind: 'video', qualityCode: '32', qualityLabel: '480P', codec: 'H.264', container: 'mp4', width: 854, height: 480, fps: 25, bitrate: 500_000, hdrType: null, audioChannels: null, sampleRate: null, estimatedSize: 8_000_000, authRequired: false, premiumRequired: false, accessRequirement: 'none', verifiedAt: null, compatibleDevices: [], compatibilityNote: '广泛兼容' },
  ],
  audios: [
    { id: 'aac', partId: 'part-1', kind: 'audio', qualityCode: '30280', qualityLabel: '高码率', codec: 'AAC', container: 'm4a', width: null, height: null, fps: null, bitrate: 188_000, hdrType: null, audioChannels: 2, sampleRate: 48000, estimatedSize: 5_100_000, authRequired: false, premiumRequired: false, accessRequirement: 'none', verifiedAt: null, compatibleDevices: [], compatibilityNote: '广泛兼容' },
  ],
}

describe('StreamSelector', () => {
  it('shows codecs separately at the same quality and selects compatible preset', async () => {
    const wrapper = mount(StreamSelector, {
      props: { streams, preset: 'custom', selectedVideoId: 'av1', selectedAudioId: 'aac' },
    })
    expect(wrapper.text()).toContain('AV1')
    expect(wrapper.text()).toContain('H.264')
    expect(wrapper.text()).toContain('MP4 · H.264 · AAC')
    expect(wrapper.text()).toContain('大会员权益')
    const presetButton = wrapper.findAll('.preset-list button').find((button) => button.text().includes('最佳兼容'))
    expect(presetButton).toBeDefined()
    await presetButton?.trigger('click')
    expect(wrapper.emitted('update:selectedVideoId')?.at(-1)).toEqual(['h264'])
    expect(wrapper.emitted('update:selectedAudioId')?.at(-1)).toEqual(['aac'])
  })

  it('respects the configured minimum height for the smallest preset', async () => {
    const wrapper = mount(StreamSelector, {
      props: {
        streams,
        preset: 'custom',
        selectedVideoId: 'h264',
        selectedAudioId: 'aac',
        minimumResolutionHeight: 720,
      },
    })
    const presetButton = wrapper.findAll('.preset-list button').find((button) => button.text().includes('最小体积'))
    await presetButton?.trigger('click')
    expect(wrapper.emitted('update:selectedVideoId')?.at(-1)).toEqual(['av1'])
  })

  it('warns once and honestly falls back when no stream reaches the threshold', async () => {
    const warning = vi.spyOn(ElMessage, 'warning').mockImplementation(() => undefined as never)
    const onlyLow: StreamCollection = { ...streams, videos: [streams.videos[2]!] }
    const wrapper = mount(StreamSelector, {
      props: {
        streams: onlyLow,
        preset: 'custom',
        selectedVideoId: null,
        selectedAudioId: 'aac',
        minimumResolutionHeight: 1080,
      },
    })
    const presetButton = wrapper.findAll('.preset-list button').find((button) => button.text().includes('最小体积'))
    await presetButton?.trigger('click')
    await presetButton?.trigger('click')
    expect(wrapper.emitted('update:selectedVideoId')?.at(-1)).toEqual(['small-480'])
    expect(warning).toHaveBeenCalledTimes(1)
    expect(warning.mock.calls[0]?.[0]).toContain('1080P')
    warning.mockRestore()
  })

  it('turns a direct stream selection into custom mode', async () => {
    const wrapper = mount(StreamSelector, {
      props: { streams, preset: 'best_quality', selectedVideoId: 'h264', selectedAudioId: 'aac' },
    })
    const av1MobileCard = wrapper.findAll('.stream-card').find((card) => card.text().includes('AV1'))
    await av1MobileCard?.trigger('click')
    expect(wrapper.emitted('update:preset')?.at(-1)).toEqual(['custom'])
    expect(wrapper.emitted('update:selectedVideoId')?.at(-1)).toEqual(['av1'])
  })

  it('offers an explicit video-only choice and switches to custom mode', async () => {
    const wrapper = mount(StreamSelector, {
      props: { streams, preset: 'best_quality', selectedVideoId: 'h264', selectedAudioId: 'aac' },
    })

    await wrapper.get('[data-testid="select-no-audio"]').trigger('click')

    expect(wrapper.emitted('update:preset')?.at(-1)).toEqual(['custom'])
    expect(wrapper.emitted('update:selectedAudioId')?.at(-1)).toEqual([null])
  })

  it('offers an explicit small-range check for only unverified selected streams', async () => {
    const wrapper = mount(StreamSelector, {
      props: { streams, preset: 'custom', selectedVideoId: 'h264', selectedAudioId: 'aac' },
    })

    const verifyButton = wrapper.get('[data-testid="verify-selected-streams"]')
    await verifyButton.trigger('click')

    expect(wrapper.emitted('verify')?.at(-1)).toEqual([['aac']])
    expect(verifyButton.text()).toContain('小范围验证')
  })

  it('opens playback only for a selected stream with DASH preview metadata', async () => {
    const playable: StreamCollection = {
      ...streams,
      videos: streams.videos.map((stream) => stream.id === 'h264'
        ? { ...stream, mimeType: 'video/mp4', codecString: 'avc1.640032', previewSupported: true }
        : stream),
      audios: streams.audios.map((stream) => ({
        ...stream,
        mimeType: 'audio/mp4',
        codecString: 'mp4a.40.2',
        previewSupported: true,
      })),
    }
    const wrapper = mount(StreamSelector, {
      props: { streams: playable, preset: 'custom', selectedVideoId: 'h264', selectedAudioId: 'aac' },
    })

    const previewButton = wrapper.get('[data-testid="open-video-preview"]')
    expect(previewButton.attributes('disabled')).toBeUndefined()
    await previewButton.trigger('click')
    expect(wrapper.emitted('preview')).toHaveLength(1)
    const audioPreviewButton = wrapper.get('[data-testid="open-audio-preview"]')
    expect(audioPreviewButton.attributes('disabled')).toBeUndefined()
    await audioPreviewButton.trigger('click')
    expect(wrapper.emitted('audio-preview')).toHaveLength(1)
  })

  it('keeps video preview available while clearly marking a video-only fallback', async () => {
    const playableVideo: StreamCollection = {
      ...streams,
      videos: streams.videos.map((stream) => stream.id === 'h264'
        ? { ...stream, mimeType: 'video/mp4', codecString: 'avc1.640032', previewSupported: true }
        : stream),
    }
    const wrapper = mount(StreamSelector, {
      props: { streams: playableVideo, preset: 'custom', selectedVideoId: 'h264', selectedAudioId: 'aac' },
    })

    const previewButton = wrapper.get('[data-testid="open-video-preview"]')
    expect(previewButton.attributes('disabled')).toBeUndefined()
    expect(wrapper.text()).toContain('所选音轨缺少在线预览信息')
    expect(wrapper.text()).toContain('临时使用无音轨模式')
    await previewButton.trigger('click')
    expect(wrapper.emitted('preview')).toHaveLength(1)
  })
})
