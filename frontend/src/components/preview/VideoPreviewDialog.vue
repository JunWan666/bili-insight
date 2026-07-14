<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { RefreshRight, VideoPlay } from '@element-plus/icons-vue'
import { previewApi } from '@/api'
import { toApiError } from '@/api/errors'
import type { MediaStream, PreviewSession, VideoDetail, VideoPart } from '@/types/api'
import { formatBitrate, formatDuration } from '@/utils/format'

interface PlayerLike {
  attach(element: HTMLMediaElement): Promise<void>
  configure(config: Record<string, unknown>): void
  load(uri: string): Promise<void>
  destroy(): Promise<void>
  addEventListener(type: string, listener: (event: Event) => void): void
}

interface ActivePlayback {
  generation: number
  session: PreviewSession
  player: PlayerLike | null
  media: HTMLVideoElement | null
}

const props = defineProps<{
  modelValue: boolean
  video: VideoDetail
  part: VideoPart
  videoStream: MediaStream | null
  audioStream: MediaStream | null
  accessMode: 'anonymous' | 'authenticated'
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const videoElement = ref<HTMLVideoElement | null>(null)
const loading = ref(false)
const errorMessage = ref<string | null>(null)
const errorAction = ref<string | null>(null)
const session = ref<PreviewSession | null>(null)
let activePlayback: ActivePlayback | null = null
let cleanupQueue: Promise<void> = Promise.resolve()
let generation = 0

const selectedLabel = computed(() => {
  const stream = props.videoStream
  if (!stream) return '未选择视频流'
  const resolution = stream.width && stream.height ? `${stream.width}×${stream.height}` : stream.qualityLabel
  return `${stream.qualityLabel} · ${resolution} · ${stream.codec}`
})

function hasPreviewMetadata(stream: MediaStream | null): stream is MediaStream {
  return Boolean(stream?.previewSupported && stream.mimeType && stream.codecString)
}

function codecSupported(stream: MediaStream | null): boolean | null {
  if (!stream?.mimeType || !stream.codecString || typeof MediaSource === 'undefined') return null
  return MediaSource.isTypeSupported(`${stream.mimeType}; codecs="${stream.codecString}"`)
}

const videoCodecSupported = computed(() => codecSupported(props.videoStream))
const audioCodecSupported = computed(() => codecSupported(props.audioStream))
const audioOmissionReason = computed<string | null>(() => {
  const stream = props.audioStream
  if (!stream) return null
  if (!hasPreviewMetadata(stream)) {
    return '所选音轨缺少预览元数据，本次将仅播放视频；下载不受影响。'
  }
  if (audioCodecSupported.value === false) {
    return `当前浏览器不支持所选 ${stream.codec} 音轨，本次将仅播放视频；可改选 AAC 音轨。`
  }
  return null
})
const previewAudioStream = computed(() => audioOmissionReason.value ? null : props.audioStream)
const audioLabel = computed(() => {
  const stream = props.audioStream
  if (!stream) return '无音轨'
  const label = `${stream.codec} · ${formatBitrate(stream.bitrate)}`
  return audioOmissionReason.value ? `${label} · 本次不加载` : label
})

const compatibilityCopy = computed(() => {
  if (videoCodecSupported.value === false) {
    return '当前浏览器或硬件无法解码所选视频编码，可改选 H.264 规格或下载后播放。'
  }
  if (audioOmissionReason.value) return audioOmissionReason.value
  if (videoCodecSupported.value === true && (!props.audioStream || audioCodecSupported.value === true)) {
    return '当前浏览器报告支持所选视频和音频编码。'
  }
  return '播放器将在加载时继续检查所选视频和音频编码。'
})
const compatibilityWarning = computed(() => (
  videoCodecSupported.value === false || audioOmissionReason.value !== null
))

function ownsPlayback(resources: ActivePlayback): boolean {
  return activePlayback === resources && generation === resources.generation
}

function canContinue(resources: ActivePlayback): boolean {
  return ownsPlayback(resources) && props.modelValue
}

function resetMedia(media: HTMLVideoElement): void {
  media.pause()
  media.removeAttribute('src')
  media.load()
}

function releaseCurrent(): Promise<void> {
  const cleanup = cleanupQueue.then(async () => {
    const current = activePlayback
    activePlayback = null
    if (current && session.value?.id === current.session.id) session.value = null

    const media = current?.media ?? videoElement.value
    if (media) resetMedia(media)
    if (!current) return

    await Promise.all([
      current.player?.destroy().catch(() => undefined) ?? Promise.resolve(),
      previewApi.remove(current.session.id).catch(() => undefined),
    ])
  })
  cleanupQueue = cleanup.catch(() => undefined)
  return cleanupQueue
}

function setPlayerError(resources?: ActivePlayback): void {
  if (resources && !ownsPlayback(resources)) return
  errorMessage.value = '所选规格未能在当前浏览器中播放'
  errorAction.value = '可改选 H.264 + AAC 规格，或继续下载后使用本地播放器观看'
  loading.value = false
}

async function startPlayback(): Promise<void> {
  const requestGeneration = ++generation
  await releaseCurrent()
  if (requestGeneration !== generation || !props.modelValue) return
  if (!props.videoStream) {
    errorMessage.value = '请先选择一个视频流'
    errorAction.value = '返回媒体流列表选择清晰度后重试'
    return
  }
  const selectedVideo = props.videoStream
  if (!hasPreviewMetadata(selectedVideo)) {
    errorMessage.value = '该媒体流缺少浏览器预览所需的索引信息'
    errorAction.value = '重新解析媒体流，或直接创建下载任务'
    return
  }
  if (videoCodecSupported.value === false) {
    errorMessage.value = '当前浏览器不支持所选视频编码'
    errorAction.value = '改选 H.264 视频规格，或继续下载后使用本地播放器观看'
    return
  }

  loading.value = true
  errorMessage.value = null
  errorAction.value = null
  const selectedAudio = previewAudioStream.value
  try {
    const created = await previewApi.create({
      videoStreamId: selectedVideo.id,
      audioStreamId: selectedAudio?.id ?? null,
      accessMode: props.accessMode,
    })
    if (requestGeneration !== generation || !props.modelValue) {
      await previewApi.remove(created.id).catch(() => undefined)
      return
    }
    const resources: ActivePlayback = {
      generation: requestGeneration,
      session: created,
      player: null,
      media: null,
    }
    activePlayback = resources
    session.value = created
    await nextTick()
    if (!canContinue(resources)) return
    const media = videoElement.value
    if (!media) throw new Error('Preview media element is unavailable')
    resources.media = media
    const { default: shaka } = await import('shaka-player')
    if (!canContinue(resources)) return
    shaka.polyfill.installAll()
    if (!shaka.Player.isBrowserSupported()) {
      setPlayerError(resources)
      return
    }
    const instance = new shaka.Player() as PlayerLike
    resources.player = instance
    instance.addEventListener('error', () => setPlayerError(resources))
    instance.configure({
      abr: { enabled: false },
      streaming: { bufferingGoal: 24, rebufferingGoal: 2, retryParameters: { maxAttempts: 3 } },
    })
    await instance.attach(media)
    if (!canContinue(resources)) return
    await instance.load(created.manifestUrl)
    if (!canContinue(resources)) return
    loading.value = false
    await media.play().catch(() => undefined)
  } catch (error) {
    if (requestGeneration !== generation || !props.modelValue) return
    const apiError = toApiError(error)
    errorMessage.value = apiError.message
    errorAction.value = apiError.action
    loading.value = false
  }
}

function close(): void {
  emit('update:modelValue', false)
}

function retry(): void {
  void startPlayback()
}

watch(
  () => [props.modelValue, props.videoStream?.id, props.audioStream?.id, props.accessMode] as const,
  ([open]) => {
    if (open) void startPlayback()
    else {
      generation += 1
      loading.value = false
      void releaseCurrent()
    }
  },
  { flush: 'post', immediate: true },
)

onBeforeUnmount(() => {
  generation += 1
  void releaseCurrent()
})
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    class="preview-dialog"
    width="min(1120px, calc(100vw - 40px))"
    append-to-body
    destroy-on-close
    :close-on-click-modal="false"
    @update:model-value="close"
  >
    <template #header>
      <div class="preview-heading">
        <span class="preview-icon"><VideoPlay /></span>
        <div>
          <strong>播放预览</strong>
          <small>{{ part.title }} · {{ selectedLabel }}</small>
        </div>
      </div>
    </template>

    <div class="preview-stage" data-testid="preview-stage">
      <video
        v-show="!errorMessage"
        ref="videoElement"
        controls
        playsinline
        preload="metadata"
        :poster="video.coverUrl"
        data-testid="preview-video"
      />
      <div v-if="loading" class="preview-loading" role="status">
        <el-icon class="is-loading"><RefreshRight /></el-icon>
        <span>正在准备 {{ videoStream?.qualityLabel }} 预览</span>
      </div>
      <div v-if="errorMessage" class="preview-error" role="alert" data-testid="preview-error">
        <strong>{{ errorMessage }}</strong>
        <p>{{ errorAction }}</p>
        <el-button :icon="RefreshRight" @click="retry">重新加载</el-button>
      </div>
    </div>

    <div class="preview-meta">
      <span><small>视频</small><strong>{{ selectedLabel }}</strong></span>
      <span :class="{ warning: audioOmissionReason }"><small>音频</small><strong>{{ audioLabel }}</strong></span>
      <span><small>时长</small><strong>{{ formatDuration(part.duration) }}</strong></span>
      <span class="compatibility" :class="{ warning: compatibilityWarning }"><small>浏览器能力</small><strong>{{ compatibilityCopy }}</strong></span>
    </div>
  </el-dialog>
</template>

<style scoped>
.preview-heading { display: flex; align-items: center; gap: 11px; min-width: 0; }
.preview-icon { display: grid; place-items: center; flex: 0 0 auto; width: 36px; height: 36px; border-radius: 8px; background: var(--brand); color: #fff; }
.preview-icon svg { width: 20px; height: 20px; }
.preview-heading strong, .preview-heading small { display: block; }
.preview-heading strong { font-size: 16px; }
.preview-heading small { margin-top: 3px; color: var(--text-tertiary); overflow-wrap: anywhere; }
.preview-stage { position: relative; display: grid; place-items: center; width: 100%; aspect-ratio: 16 / 9; overflow: hidden; background: #090a0d; }
.preview-stage video { width: 100%; height: 100%; background: #090a0d; object-fit: contain; }
.preview-loading, .preview-error { position: absolute; inset: 0; display: grid; place-content: center; justify-items: center; gap: 12px; padding: 28px; background: rgba(9, 10, 13, .9); color: #fff; text-align: center; }
.preview-loading .el-icon { font-size: 28px; }
.preview-error strong { font-size: 17px; }
.preview-error p { max-width: 560px; margin: 0; color: #c7cad3; line-height: 1.55; }
.preview-meta { display: grid; grid-template-columns: 1.35fr 1fr .55fr 1.5fr; gap: 1px; margin-top: 1px; background: var(--line-soft); }
.preview-meta > span { min-width: 0; padding: 12px 14px; background: var(--surface); }
.preview-meta small, .preview-meta strong { display: block; }
.preview-meta small { color: var(--text-tertiary); font-size: 10px; }
.preview-meta strong { margin-top: 5px; font-size: 12px; overflow-wrap: anywhere; }
.preview-meta .warning strong { color: var(--warning); }

@media (max-width: 767px) {
  .preview-stage { aspect-ratio: 16 / 10; }
  .preview-meta { grid-template-columns: 1fr 1fr; }
  .preview-meta .compatibility { grid-column: 1 / -1; }
}
</style>

<style>
.preview-dialog .el-dialog__body { padding: 0; }
@media (max-width: 767px) {
  .preview-dialog { width: calc(100vw - 20px) !important; margin-top: max(12px, env(safe-area-inset-top)) !important; }
  .preview-dialog .el-dialog__header { padding: 14px 16px; }
}
</style>
