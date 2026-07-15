<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Check, CircleCheck, Headset, Lock, Select, VideoCamera, VideoPlay } from '@element-plus/icons-vue'
import type { DownloadPreset, MediaStream, StreamCollection } from '@/types/api'
import { useMobile } from '@/composables/useMobile'
import { formatBitrate, formatBytes } from '@/utils/format'
import { codecFamily } from '@/utils/mediaCompatibility'

const props = defineProps<{
  streams: StreamCollection
  preset: DownloadPreset
  selectedVideoId: string | null
  selectedAudioId: string | null
  minimumResolutionHeight?: 360 | 480 | 720 | 1080 | null
  verifying?: boolean
}>()

const emit = defineEmits<{
  'update:preset': [value: DownloadPreset]
  'update:selectedVideoId': [value: string | null]
  'update:selectedAudioId': [value: string | null]
  configure: []
  'audio-download': []
  'audio-preview': []
  preview: []
  verify: [streamIds: string[]]
}>()

const presets: Array<{ value: DownloadPreset; label: string; hint: string }> = [
  { value: 'best_quality', label: '最佳画质', hint: '最高可访问规格' },
  { value: 'best_compatibility', label: '最佳兼容', hint: 'MP4 · H.264 · AAC' },
  { value: 'smallest', label: '最小体积', hint: '优先压缩率' },
  { value: 'audio_only', label: '仅音频', hint: '不选择视频流' },
  { value: 'custom', label: '自定义', hint: '手动选择音视频' },
]

const selectedVideo = computed(() => props.streams.videos.find((stream) => stream.id === props.selectedVideoId) ?? null)
const selectedAudio = computed(() => props.streams.audios.find((stream) => stream.id === props.selectedAudioId) ?? null)
const { isMobile } = useMobile()
const selectedVideoPreviewable = computed(() => hasPreviewMetadata(selectedVideo.value))
const selectedAudioPreviewable = computed(() => hasPreviewMetadata(selectedAudio.value))
const audioPreviewFallback = computed(() => (
  selectedAudio.value && !hasPreviewMetadata(selectedAudio.value)
    ? '所选音轨缺少在线预览信息，播放时将临时使用无音轨模式；下载不受影响。'
    : null
))
const previewTooltip = computed(() => {
  if (!selectedVideoPreviewable.value) return '该视频规格暂不具备浏览器预览信息'
  if (audioPreviewFallback.value) return audioPreviewFallback.value
  return '使用当前所选视频和音频规格播放'
})
const unverifiedSelectedStreamIds = computed(() => (
  [selectedVideo.value, selectedAudio.value]
    .filter((stream): stream is MediaStream => Boolean(stream && !stream.verifiedAt))
    .map((stream) => stream.id)
))
const hasSelectedStream = computed(() => Boolean(selectedVideo.value || selectedAudio.value))
const lastResolutionFallback = ref<string | null>(null)

function hasPreviewMetadata(stream: MediaStream | null): stream is MediaStream {
  return Boolean(stream?.previewSupported && stream.mimeType && stream.codecString)
}

function videoScore(stream: MediaStream): number {
  return (stream.height ?? 0) * 1_000_000_000 + (stream.fps ?? 0) * 1_000_000 + (stream.bitrate ?? 0)
}

function chooseHighest(streams: MediaStream[]): MediaStream | undefined {
  return [...streams].sort((a, b) => videoScore(b) - videoScore(a))[0]
}

function chooseAudio(streams: MediaStream[], compatible = false): MediaStream | undefined {
  const filtered = compatible ? streams.filter((stream) => codecFamily(stream.codec) === 'aac') : streams
  return [...(filtered.length ? filtered : streams)].sort((a, b) => (b.bitrate ?? 0) - (a.bitrate ?? 0))[0]
}

function applyPreset(value: DownloadPreset): void {
  emit('update:preset', value)
  if (value === 'custom') return
  if (value === 'audio_only') {
    emit('update:selectedVideoId', null)
    emit('update:selectedAudioId', chooseAudio(props.streams.audios)?.id ?? null)
    return
  }
  if (value === 'best_compatibility') {
    const compatible = props.streams.videos.filter((stream) => codecFamily(stream.codec) === 'h264')
    emit('update:selectedVideoId', chooseHighest(compatible.length ? compatible : props.streams.videos)?.id ?? null)
    emit('update:selectedAudioId', chooseAudio(props.streams.audios, true)?.id ?? null)
    return
  }
  if (value === 'smallest') {
    const threshold = props.minimumResolutionHeight ?? null
    const eligible = threshold === null
      ? props.streams.videos
      : props.streams.videos.filter((stream) => (stream.height ?? 0) >= threshold)
    const source = eligible.length ? eligible : props.streams.videos
    if (threshold !== null && !eligible.length && props.streams.videos.length) {
      const fallbackKey = `${props.streams.partId}:${threshold}`
      if (lastResolutionFallback.value !== fallbackKey) {
        ElMessage.warning(`没有高度达到 ${threshold}P 的可用视频流，最小体积预设已回退到现有规格。`)
        lastResolutionFallback.value = fallbackKey
      }
    } else {
      lastResolutionFallback.value = null
    }
    const candidates = [...source].sort((a, b) => (a.estimatedSize ?? Number.MAX_VALUE) - (b.estimatedSize ?? Number.MAX_VALUE))
    emit('update:selectedVideoId', candidates[0]?.id ?? null)
    emit('update:selectedAudioId', [...props.streams.audios].sort((a, b) => (a.estimatedSize ?? Number.MAX_VALUE) - (b.estimatedSize ?? Number.MAX_VALUE))[0]?.id ?? null)
    return
  }
  emit('update:selectedVideoId', chooseHighest(props.streams.videos)?.id ?? null)
  emit('update:selectedAudioId', chooseAudio(props.streams.audios)?.id ?? null)
}

function selectVideo(stream: MediaStream): void {
  emit('update:preset', 'custom')
  emit('update:selectedVideoId', stream.id)
}

function selectAudio(stream: MediaStream): void {
  emit('update:preset', 'custom')
  emit('update:selectedAudioId', stream.id)
}

function selectNoAudio(): void {
  emit('update:preset', 'custom')
  emit('update:selectedAudioId', null)
}

function resolution(stream: MediaStream): string {
  return stream.width && stream.height ? `${stream.width}×${stream.height}` : '暂无'
}

function compatibility(stream: MediaStream): string {
  if (stream.compatibilityNote) return stream.compatibilityNote
  if (/avc|h\.264|aac|mp4a/i.test(stream.codec)) return '广泛兼容'
  if (/hevc|h\.265/i.test(stream.codec)) return '新款设备兼容'
  if (/av1/i.test(stream.codec)) return '需较新设备'
  return '请确认目标设备'
}

function accessLabel(stream: MediaStream): string {
  const labels = {
    none: '匿名可用',
    login: '登录权益',
    premium: '大会员权益',
    special: '特定权益',
  }
  return labels[stream.accessRequirement]
}

function accessTagType(stream: MediaStream): 'success' | 'warning' | 'danger' {
  if (stream.accessRequirement === 'none') return 'success'
  if (stream.accessRequirement === 'special') return 'danger'
  return 'warning'
}

watch(
  () => [props.streams, props.minimumResolutionHeight],
  () => applyPreset(props.preset),
  { immediate: true },
)
</script>

<template>
  <div class="stream-selector">
    <div class="preset-list" role="radiogroup" aria-label="下载预设">
      <button
        v-for="item in presets"
        :key="item.value"
        type="button"
        :class="{ active: preset === item.value }"
        :aria-pressed="preset === item.value"
        @click="applyPreset(item.value)"
      >
        <span class="preset-check"><Check /></span>
        <span><strong>{{ item.label }}</strong><small>{{ item.hint }}</small></span>
      </button>
    </div>

    <div class="stream-heading">
      <div><h3>视频流</h3><p>同一清晰度的不同编码会分别列出。</p></div>
      <el-tag effect="plain">{{ streams.videos.length }} 个可访问规格</el-tag>
    </div>

    <div v-if="streams.videos.length" class="desktop-table">
      <el-table :data="streams.videos" row-key="id">
        <el-table-column width="54">
          <template #default="{ row }">
            <button class="select-radio" :class="{ selected: row.id === selectedVideoId }" type="button" :aria-label="`选择 ${row.qualityLabel} ${row.codec}`" @click="selectVideo(row)"><Select /></button>
          </template>
        </el-table-column>
        <el-table-column label="清晰度" min-width="130">
          <template #default="{ row }"><strong>{{ row.qualityLabel }}</strong><small class="cell-sub">ID {{ row.qualityCode }}</small></template>
        </el-table-column>
        <el-table-column label="画面" min-width="135">
          <template #default="{ row }">{{ resolution(row) }}<small class="cell-sub">{{ row.fps ? `${row.fps} fps` : '帧率暂无' }} · {{ row.hdrType || 'SDR' }}</small></template>
        </el-table-column>
        <el-table-column label="编码" min-width="125">
          <template #default="{ row }"><strong>{{ row.codec }}</strong><small class="cell-sub">{{ compatibility(row) }}</small></template>
        </el-table-column>
        <el-table-column label="码率 / 体积" min-width="125">
          <template #default="{ row }">{{ formatBitrate(row.bitrate) }}<small class="cell-sub">约 {{ formatBytes(row.estimatedSize) }}</small></template>
        </el-table-column>
        <el-table-column label="状态" min-width="120">
          <template #default="{ row }">
            <el-tag size="small" :type="accessTagType(row)" effect="plain"><el-icon v-if="row.accessRequirement !== 'none'"><Lock /></el-icon> {{ accessLabel(row) }}</el-tag>
            <small class="cell-sub">{{ row.verifiedAt ? '已读取验证' : '待任务前验证' }}</small>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div v-if="streams.videos.length" class="mobile-streams">
      <button
        v-for="stream in streams.videos"
        :key="stream.id"
        class="stream-card"
        data-testid="stream-card"
        :class="{ selected: stream.id === selectedVideoId }"
        type="button"
        @click="selectVideo(stream)"
      >
        <span class="mobile-select"><Check /></span>
        <span class="quality"><strong>{{ stream.qualityLabel }}</strong><small>{{ resolution(stream) }} · {{ stream.fps ? `${stream.fps} fps` : '帧率暂无' }}</small></span>
        <span class="codec"><strong>{{ stream.codec }}</strong><small>{{ stream.hdrType || 'SDR' }} · {{ compatibility(stream) }}</small></span>
        <span class="size"><strong>约 {{ formatBytes(stream.estimatedSize) }}</strong><small>{{ formatBitrate(stream.bitrate) }}</small></span>
        <span class="mobile-status">
          <el-tag size="small" :type="accessTagType(stream)" effect="plain">{{ accessLabel(stream) }}</el-tag>
          <small>{{ stream.verifiedAt ? '已读取验证' : '待小范围验证' }}</small>
        </span>
      </button>
    </div>

    <el-empty v-else :image-size="80" description="当前分 P 没有可访问的视频流" />

    <div class="stream-heading audio-heading">
      <div><h3>音频流</h3><p>选择随视频合并或单独输出的音轨。</p></div>
    </div>
    <div class="audio-grid">
      <button
        type="button"
        :class="{ selected: selectedAudioId === null && selectedVideoId !== null }"
        data-testid="select-no-audio"
        @click="selectNoAudio"
      >
        <span class="mobile-select"><Check /></span>
        <span><strong>不附加音频</strong><small>仅保存视频轨，不进行音频合并</small></span>
      </button>
      <button
        v-for="stream in streams.audios"
        :key="stream.id"
        type="button"
        :class="{ selected: stream.id === selectedAudioId }"
        @click="selectAudio(stream)"
      >
        <span class="mobile-select"><Check /></span>
        <span><strong>{{ stream.codec }} · {{ formatBitrate(stream.bitrate) }}</strong><small>{{ stream.sampleRate ? `${Math.round(stream.sampleRate / 1000)} kHz` : '采样率暂无' }} · {{ stream.audioChannels ? `${stream.audioChannels} 声道` : '声道暂无' }} · 约 {{ formatBytes(stream.estimatedSize) }}</small></span>
      </button>
      <p v-if="!streams.audios.length" class="muted">没有单独的音频流可供选择。</p>
    </div>

    <div class="selection-bar">
      <div>
        <small>当前选择</small>
        <strong v-if="selectedVideo">{{ selectedVideo.qualityLabel }} · {{ selectedVideo.codec }}</strong>
        <strong v-else>仅音频</strong>
        <span> + {{ selectedAudio ? `${selectedAudio.codec} ${formatBitrate(selectedAudio.bitrate)}` : '不附加音频' }}</span>
        <small v-if="audioPreviewFallback" class="preview-fallback">{{ audioPreviewFallback }}</small>
      </div>
      <div class="selection-actions">
        <el-tooltip :disabled="isMobile" :content="selectedAudioPreviewable ? '在线试听当前所选音轨' : '该音轨暂不具备在线试听信息'" placement="top">
          <el-button
            v-if="selectedAudio"
            size="large"
            :icon="Headset"
            :disabled="!selectedAudioPreviewable"
            data-testid="open-audio-preview"
            @click="$emit('audio-preview')"
          >
            试听音频
          </el-button>
        </el-tooltip>
        <el-button
          v-if="selectedAudio"
          size="large"
          :icon="Headset"
          @click="$emit('audio-download')"
        >
          下载音频
        </el-button>
        <el-tooltip
          :disabled="isMobile"
          :content="previewTooltip"
          placement="top"
        >
          <el-button
            size="large"
            :icon="VideoPlay"
            :disabled="!selectedVideoPreviewable"
            data-testid="open-video-preview"
            @click="$emit('preview')"
          >
            立即播放
          </el-button>
        </el-tooltip>
        <el-tooltip :disabled="isMobile" content="仅读取极小范围媒体数据，不下载完整文件" placement="top">
          <el-button
            size="large"
            :icon="CircleCheck"
            :loading="verifying"
            :disabled="!hasSelectedStream || unverifiedSelectedStreamIds.length === 0"
            data-testid="verify-selected-streams"
            @click="$emit('verify', unverifiedSelectedStreamIds)"
          >
            {{ unverifiedSelectedStreamIds.length ? '小范围验证' : '已读取验证' }}
          </el-button>
        </el-tooltip>
        <el-button
          type="primary"
          size="large"
          :icon="VideoCamera"
          :disabled="!hasSelectedStream"
          data-testid="open-download-config"
          @click="$emit('configure')"
        >
          配置并下载
        </el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.preset-list { display: grid; grid-template-columns: repeat(5, 1fr); gap: 9px; margin-bottom: 29px; }
.preset-list button { display: flex; align-items: center; gap: 9px; min-height: 66px; padding: 10px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); color: var(--text-primary); text-align: left; cursor: pointer; }
.preset-list button.active { border-color: var(--brand); background: var(--brand-soft); }
.preset-check { display: grid; place-items: center; flex: 0 0 auto; width: 18px; height: 18px; border: 1px solid var(--line); border-radius: 50%; color: transparent; }
.active .preset-check { border-color: var(--brand); background: var(--brand); color: #fff; }
.preset-check svg { width: 11px; }
.preset-list strong, .preset-list small { display: block; }
.preset-list strong { font-size: 12px; }
.preset-list small { margin-top: 3px; color: var(--text-tertiary); font-size: 10px; line-height: 1.3; }
.stream-heading { display: flex; align-items: center; justify-content: space-between; gap: 18px; margin: 8px 0 14px; }
.stream-heading h3 { margin: 0; font-size: 16px; }
.stream-heading p { margin: 4px 0 0; color: var(--text-tertiary); font-size: 12px; }
.audio-heading { margin-top: 30px; }
.cell-sub { display: block; margin-top: 4px; color: var(--text-tertiary); font-size: 11px; }
.select-radio { display: grid; place-items: center; width: 44px; height: 44px; border: 1px solid var(--line); border-radius: 50%; background: var(--surface); color: transparent; cursor: pointer; }
.select-radio.selected { border-color: var(--brand); background: var(--brand); color: white; }
.select-radio svg { width: 15px; }
.mobile-streams { display: none; }
.audio-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 9px; }
.audio-grid button { display: flex; align-items: center; gap: 10px; min-height: 67px; padding: 12px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); color: var(--text-primary); text-align: left; cursor: pointer; }
.audio-grid button.selected { border-color: var(--brand); background: var(--brand-soft); }
.mobile-select { display: grid; place-items: center; flex: 0 0 auto; width: 20px; height: 20px; border: 1px solid var(--line); border-radius: 50%; color: transparent; }
.selected .mobile-select { border-color: var(--brand); background: var(--brand); color: white; }
.mobile-select svg { width: 12px; }
.audio-grid strong, .audio-grid small { display: block; }
.audio-grid strong { font-size: 12px; }
.audio-grid small { margin-top: 4px; color: var(--text-tertiary); font-size: 11px; line-height: 1.45; }
.selection-bar { position: sticky; bottom: 14px; z-index: 4; display: flex; align-items: center; justify-content: space-between; gap: 18px; margin-top: 26px; padding: 14px 16px; border: 1px solid var(--line); border-radius: 15px; background: color-mix(in srgb, var(--surface) 93%, transparent); box-shadow: 0 10px 30px rgba(31, 36, 51, .12); backdrop-filter: blur(15px); }
.selection-bar > div { min-width: 0; }
.selection-bar small { display: block; margin-bottom: 4px; color: var(--text-tertiary); }
.selection-bar strong { overflow-wrap: anywhere; }
.selection-bar span { color: var(--text-secondary); }
.selection-bar .preview-fallback { max-width: 620px; margin: 6px 0 0; color: var(--warning); line-height: 1.45; }
.selection-actions { display: flex; align-items: center; gap: 9px; }

@media (max-width: 1000px) {
  .preset-list { grid-template-columns: repeat(3, 1fr); }
  .audio-grid { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 767px) {
  .preset-list { display: flex; margin-inline: -16px; padding-inline: 16px; overflow-x: auto; scroll-snap-type: x mandatory; scrollbar-width: none; }
  .preset-list::-webkit-scrollbar { display: none; }
  .preset-list button { flex: 0 0 142px; min-height: 64px; scroll-snap-align: start; }
  .desktop-table { display: none; }
  .mobile-streams { display: grid; gap: 10px; }
  .stream-card { position: relative; display: grid; grid-template-columns: auto 1fr auto; grid-template-areas: "pick quality tag" "pick codec size"; align-items: center; gap: 11px 12px; min-height: 116px; padding: 15px; border: 1px solid var(--line); border-radius: 15px; background: var(--surface); color: var(--text-primary); text-align: left; cursor: pointer; }
  .stream-card.selected { border-color: var(--brand); background: var(--brand-soft); box-shadow: 0 0 0 1px var(--brand) inset; }
  .stream-card .mobile-select { grid-area: pick; }
  .quality { grid-area: quality; }
  .codec { grid-area: codec; }
  .size { grid-area: size; text-align: right; }
  .mobile-status { grid-area: tag; justify-self: end; text-align: right; }
  .mobile-status .el-tag { display: inline-flex; }
  .stream-card strong, .stream-card small { display: block; }
  .stream-card small { margin-top: 3px; color: var(--text-tertiary); font-size: 11px; line-height: 1.3; }
  .audio-grid { grid-template-columns: 1fr; }
  .audio-grid button { min-height: 70px; }
  .selection-bar { position: static; bottom: auto; display: grid; grid-template-columns: 1fr; margin-inline: -4px; }
  .selection-actions { display: grid; grid-template-columns: 1fr; }
  .selection-actions .el-button { width: 100%; min-height: 48px; margin-left: 0; }
}
@media (max-width: 374px) {
  .stream-card { grid-template-columns: auto 1fr; grid-template-areas: "pick quality" "pick codec" ". size" ". tag"; }
  .stream-card .size { text-align: left; }
  .mobile-status { justify-self: start; text-align: left; }
}
</style>
