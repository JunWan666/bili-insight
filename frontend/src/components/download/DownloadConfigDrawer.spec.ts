import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { ElNotification } from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DownloadConfigDrawer from './DownloadConfigDrawer.vue'
import type { Job, MediaStream, VideoDetail, VideoPart } from '@/types/api'

const createDownload = vi.hoisted(() => vi.fn())

vi.mock('@/stores/jobs', () => ({
  useJobsStore: () => ({ createDownload }),
}))

const part = { id: 'part-1', videoId: 'video-1', cid: '1', pageNumber: 1, title: '第一 P', duration: 30 } as VideoPart
const video = {
  id: 'video-1',
  bvid: 'BV1TEST',
  title: '测试视频',
  parts: [part],
} as VideoDetail

function stream(kind: 'video' | 'audio', codec: string): MediaStream {
  return {
    id: `${kind}-${codec}`,
    partId: part.id,
    kind,
    qualityCode: '1',
    qualityLabel: kind === 'video' ? '1080P' : codec,
    codec,
    container: 'm4s',
    width: kind === 'video' ? 1920 : null,
    height: kind === 'video' ? 1080 : null,
    fps: kind === 'video' ? 25 : null,
    bitrate: 128_000,
    hdrType: null,
    audioChannels: kind === 'audio' ? 2 : null,
    sampleRate: kind === 'audio' ? 48_000 : null,
    estimatedSize: 1_000,
    authRequired: false,
    premiumRequired: false,
    accessRequirement: 'none',
    verifiedAt: null,
    compatibleDevices: [],
    compatibilityNote: null,
  }
}

const reusedJob = {
  id: 'job-reused',
  type: 'download',
  status: 'completed',
  phase: 'completed',
} as Job

describe('DownloadConfigDrawer', () => {
  beforeEach(() => {
    createDownload.mockReset()
    createDownload.mockResolvedValue({ job: reusedJob, reused: true })
  })

  it('forces unsafe FLAC + MP4 copy to transcode and reports a reused task', async () => {
    const info = vi.spyOn(ElNotification, 'info').mockImplementation(() => undefined as never)
    const wrapper = mount(DownloadConfigDrawer, {
      props: {
        modelValue: false,
        video,
        part,
        videoStream: stream('video', 'H.264/AVC'),
        audioStream: stream('audio', 'FLAC'),
        accessMode: 'anonymous',
        preset: 'best_quality',
      },
      global: {
        stubs: {
          ElDrawer: {
            template: '<div><slot name="header"/><slot/><slot name="footer"/></div>',
          },
        },
      },
    })
    await wrapper.setProps({ modelValue: true })
    await nextTick()

    expect(wrapper.text()).toContain('FLAC 音频不能安全无损封装到 MP4')
    expect(wrapper.text()).toContain('复用已有任务或产物')
    await wrapper.get('[data-testid="create-download-job"]').trigger('click')

    expect(createDownload).toHaveBeenCalledWith(expect.objectContaining({
      container: 'mp4',
      processingMode: 'transcode',
      reuseExisting: true,
      includeDanmaku: false,
    }))
    expect(info).toHaveBeenCalledWith(expect.objectContaining({ title: '已复用现有任务' }))
    expect(wrapper.emitted('created')).toEqual([['job-reused']])
    info.mockRestore()
  })

  it('lets the user disable reuse explicitly', async () => {
    const wrapper = mount(DownloadConfigDrawer, {
      props: {
        modelValue: true,
        video,
        part,
        videoStream: stream('video', 'H.264/AVC'),
        audioStream: stream('audio', 'AAC'),
        accessMode: 'anonymous',
      },
      global: {
        stubs: {
          ElDrawer: {
            template: '<div><slot name="header"/><slot/><slot name="footer"/></div>',
          },
        },
      },
    })
    await nextTick()
    const reuseSwitch = wrapper.findAll('.compact-section .el-switch')[0]
    expect(reuseSwitch).toBeDefined()
    await reuseSwitch!.trigger('click')
    await wrapper.get('[data-testid="create-download-job"]').trigger('click')
    expect(createDownload).toHaveBeenCalledWith(expect.objectContaining({ reuseExisting: false }))
  })
})
