<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElNotification } from 'element-plus'
import { Check, Files, InfoFilled, Search, VideoCamera } from '@element-plus/icons-vue'
import { videoApi } from '@/api'
import { ApiError, toApiError } from '@/api/errors'
import RequestError from '@/components/RequestError.vue'
import { useMobile } from '@/composables/useMobile'
import { useJobsStore } from '@/stores/jobs'
import type {
  CreateDownloadRequest,
  DownloadCreationResult,
  DownloadPreset,
  MediaStream,
  OutputContainer,
  StreamCollection,
  VideoDetail,
  VideoPart,
} from '@/types/api'
import { copyCompatibilityIssue, codecFamily } from '@/utils/mediaCompatibility'

type BatchPreset = Exclude<DownloadPreset, 'custom'>
type ResolutionState = 'pending' | 'loading' | 'ready' | 'error'

interface PartResolution {
  part: VideoPart
  state: ResolutionState
  summary: string
  request: CreateDownloadRequest | null
  result: DownloadCreationResult | null
}

interface BatchSnapshot {
  videoId: string
  preset: BatchPreset
  container: 'mp4' | 'mkv'
  includeSubtitle: boolean
  includeDanmaku: boolean
  includeCover: boolean
  includeMetadata: boolean
  cleanupTemporary: boolean
  reuseExisting: boolean
  filename: string
  minimumResolutionHeight: 360 | 480 | 720 | 1080 | null
  accessMode: 'anonymous' | 'authenticated'
}

const props = defineProps<{
  modelValue: boolean
  video: VideoDetail
  initialPartId?: string
  accessMode: 'anonymous' | 'authenticated'
  preset?: DownloadPreset
  defaultFilenameTemplate?: string
  defaultContainer?: 'mp4' | 'mkv'
  minimumResolutionHeight?: 360 | 480 | 720 | 1080 | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  created: [jobIds: string[]]
}>()

const router = useRouter()
const jobs = useJobsStore()
const { isMobile } = useMobile()
const selectedPartIds = ref<string[]>([])
const search = ref('')
const batchPreset = ref<BatchPreset>('best_quality')
const submitting = ref(false)
const formError = ref<ApiError | null>(null)
const filenameError = ref('')
const resolutions = ref<PartResolution[]>([])

const form = reactive({
  container: 'mp4' as 'mp4' | 'mkv',
  includeSubtitle: true,
  includeDanmaku: false,
  includeCover: true,
  includeMetadata: true,
  filenameTemplate: '{title} - P{page}',
  cleanupTemporary: true,
  reuseExisting: true,
})

const presets: Array<{ value: BatchPreset; label: string; note: string }> = [
  { value: 'best_quality', label: '最佳画质', note: '每个分 P 选择最高实际规格' },
  { value: 'best_compatibility', label: '最佳兼容', note: '优先 H.264 + AAC，必要时转码' },
  { value: 'smallest', label: '最小体积', note: '遵守最低分辨率设置后选择较小流' },
  { value: 'audio_only', label: '仅音频', note: '每个分 P 输出 M4A' },
]

const filteredParts = computed(() => {
  const keyword = search.value.trim().toLocaleLowerCase()
  if (!keyword) return props.video.parts
  return props.video.parts.filter((part) => (
    `p${part.pageNumber} ${part.title}`.toLocaleLowerCase().includes(keyword)
  ))
})
const selectedParts = computed(() => {
  const selected = new Set(selectedPartIds.value)
  return props.video.parts.filter((part) => selected.has(part.id))
})
const completedResults = computed(() => resolutions.value.filter((item) => item.result !== null))
const resolvedCount = computed(() => resolutions.value.filter((item) => item.state !== 'pending').length)
const canSubmit = computed(() => (
  selectedPartIds.value.length >= 2
  && selectedPartIds.value.length <= 20
  && !submitting.value
  && completedResults.value.length === 0
))

watch(() => props.modelValue, (open) => {
  if (!open) return
  const configuredPreset = props.preset && props.preset !== 'custom' ? props.preset : 'best_quality'
  batchPreset.value = configuredPreset
  form.container = configuredPreset === 'best_compatibility'
    ? 'mp4'
    : (props.defaultContainer ?? 'mp4')
  form.filenameTemplate = props.defaultFilenameTemplate?.trim() || '{title} - P{page}'
  form.reuseExisting = true
  filenameError.value = ''
  formError.value = null
  search.value = ''
  resolutions.value = []
  const initial = props.video.parts.find((part) => part.id === props.initialPartId)
  selectedPartIds.value = [
    ...(initial ? [initial.id] : []),
    ...props.video.parts.filter((part) => part.id !== initial?.id).map((part) => part.id),
  ].slice(0, 20)
}, { immediate: true })

watch(batchPreset, (value) => {
  if (submitting.value) return
  if (value === 'best_compatibility') form.container = 'mp4'
  resolutions.value = []
  formError.value = null
})

watch(selectedPartIds, () => {
  if (submitting.value) return
  resolutions.value = []
  formError.value = null
}, { deep: true })

function close(): void {
  if (!submitting.value) emit('update:modelValue', false)
}

function selectVisible(): void {
  if (submitting.value) return
  const visible = filteredParts.value.map((part) => part.id)
  const merged = [...new Set([...selectedPartIds.value, ...visible])]
  selectedPartIds.value = merged.slice(0, 20)
  if (merged.length > 20) ElMessage.warning('单个批次最多选择 20 个分 P；其余分 P 可在下一批继续创建。')
}

function clearSelection(): void {
  if (submitting.value) return
  selectedPartIds.value = []
}

function validateFilename(): boolean {
  const value = form.filenameTemplate.trim()
  if (!value) filenameError.value = '请输入文件名模板'
  else if (value.length > 180) filenameError.value = '文件名模板不能超过 180 个字符'
  else if (/[<>:"/\\|?*\u0000-\u001F]/.test(value)) filenameError.value = '文件名不能包含 < > : " / \\ | ? * 或控制字符'
  else filenameError.value = ''
  return !filenameError.value
}

function videoScore(stream: MediaStream): number {
  return (stream.height ?? 0) * 1_000_000_000 + (stream.fps ?? 0) * 1_000_000 + (stream.bitrate ?? 0)
}

function chooseHighest(streams: MediaStream[]): MediaStream | null {
  return [...streams].sort((left, right) => videoScore(right) - videoScore(left))[0] ?? null
}

function chooseAudio(streams: MediaStream[], compatible = false, smallest = false): MediaStream | null {
  const preferred = compatible ? streams.filter((stream) => codecFamily(stream.codec) === 'aac') : streams
  const candidates = preferred.length ? preferred : streams
  return [...candidates].sort((left, right) => {
    if (smallest) {
      return (left.estimatedSize ?? Number.MAX_SAFE_INTEGER) - (right.estimatedSize ?? Number.MAX_SAFE_INTEGER)
        || (left.bitrate ?? Number.MAX_SAFE_INTEGER) - (right.bitrate ?? Number.MAX_SAFE_INTEGER)
    }
    return (right.bitrate ?? 0) - (left.bitrate ?? 0)
  })[0] ?? null
}

function chooseSmallestVideo(
  streams: MediaStream[],
  minimumHeight: BatchSnapshot['minimumResolutionHeight'],
): { stream: MediaStream | null; fallback: string | null } {
  const eligible = minimumHeight === null
    ? streams
    : streams.filter((stream) => (stream.height ?? 0) >= minimumHeight)
  const candidates = eligible.length ? eligible : streams
  const stream = [...candidates].sort((left, right) => (
    (left.estimatedSize ?? Number.MAX_SAFE_INTEGER) - (right.estimatedSize ?? Number.MAX_SAFE_INTEGER)
    || (left.bitrate ?? Number.MAX_SAFE_INTEGER) - (right.bitrate ?? Number.MAX_SAFE_INTEGER)
  ))[0] ?? null
  return {
    stream,
    fallback: minimumHeight !== null && streams.length > 0 && eligible.length === 0
      ? `没有达到 ${minimumHeight}P 的流，已回退到现有规格`
      : null,
  }
}

function requestFor(
  part: VideoPart,
  streams: StreamCollection,
  snapshot: BatchSnapshot,
): { request: CreateDownloadRequest; fallback: string | null } {
  let videoStream: MediaStream | null = null
  let audioStream: MediaStream | null = null
  let container: OutputContainer = snapshot.container
  let fallback: string | null = null

  if (snapshot.preset === 'audio_only') {
    audioStream = chooseAudio(streams.audios)
    container = 'm4a'
  } else if (snapshot.preset === 'best_compatibility') {
    const h264 = streams.videos.filter((stream) => codecFamily(stream.codec) === 'h264')
    videoStream = chooseHighest(h264.length ? h264 : streams.videos)
    audioStream = chooseAudio(streams.audios, true)
    container = 'mp4'
  } else if (snapshot.preset === 'smallest') {
    const smallest = chooseSmallestVideo(streams.videos, snapshot.minimumResolutionHeight)
    videoStream = smallest.stream
    fallback = smallest.fallback
    audioStream = chooseAudio(streams.audios, false, true)
  } else {
    videoStream = chooseHighest(streams.videos)
    audioStream = chooseAudio(streams.audios)
  }

  if (snapshot.preset === 'audio_only' && !audioStream) {
    throw new ApiError({ code: 'AUDIO_STREAM_MISSING', message: '当前分 P 没有可用音频流', action: '移除该分 P 或改用包含视频的预设' })
  }
  if (snapshot.preset !== 'audio_only' && !videoStream) {
    throw new ApiError({ code: 'VIDEO_STREAM_MISSING', message: '当前分 P 没有可用视频流', action: '移除该分 P 或重新解析' })
  }
  const issue = copyCompatibilityIssue(container, videoStream, audioStream)
  const mustTranscode = snapshot.preset === 'best_compatibility'
    && (codecFamily(videoStream?.codec ?? '') !== 'h264'
      || (audioStream !== null && codecFamily(audioStream.codec) !== 'aac'))

  return { request: {
    videoId: snapshot.videoId,
    partId: part.id,
    videoStreamId: videoStream?.id ?? null,
    audioStreamId: audioStream?.id ?? (videoStream ? 'none' : 'auto'),
    container,
    processingMode: issue || mustTranscode ? 'transcode' : 'copy',
    accessMode: snapshot.accessMode,
    includeSubtitle: snapshot.includeSubtitle,
    includeDanmaku: snapshot.includeDanmaku,
    includeCover: snapshot.includeCover,
    includeMetadata: snapshot.includeMetadata,
    cleanupTemporary: snapshot.cleanupTemporary,
    filename: snapshot.filename,
    reuseExisting: snapshot.reuseExisting,
  }, fallback }
}

function requestSummary(request: CreateDownloadRequest, streams: StreamCollection, fallback: string | null): string {
  const videoStream = streams.videos.find((stream) => stream.id === request.videoStreamId)
  const audioStream = streams.audios.find((stream) => stream.id === request.audioStreamId)
  const source = videoStream
    ? `${videoStream.qualityLabel} · ${videoStream.codec}${audioStream ? ` + ${audioStream.codec}` : ''}`
    : `仅音频 · ${audioStream?.codec ?? '自动音轨'}`
  return `${source} · ${request.container.toUpperCase()} · ${request.processingMode === 'copy' ? '无损封装' : '兼容转码'}${fallback ? ` · ${fallback}` : ''}`
}

async function submit(): Promise<void> {
  if (!validateFilename()) return
  if (selectedPartIds.value.length < 2 || selectedPartIds.value.length > 20) {
    ElMessage.warning('请选择 2–20 个分 P')
    return
  }
  submitting.value = true
  formError.value = null
  const partsSnapshot = [...selectedParts.value]
  const snapshot: BatchSnapshot = {
    videoId: props.video.id,
    preset: batchPreset.value,
    container: form.container,
    includeSubtitle: form.includeSubtitle,
    includeDanmaku: form.includeDanmaku,
    includeCover: form.includeCover,
    includeMetadata: form.includeMetadata,
    cleanupTemporary: form.cleanupTemporary,
    reuseExisting: form.reuseExisting,
    filename: form.filenameTemplate.trim(),
    minimumResolutionHeight: props.minimumResolutionHeight ?? null,
    accessMode: props.accessMode,
  }
  resolutions.value = partsSnapshot.map((part) => ({
    part,
    state: 'pending',
    summary: '等待获取媒体流',
    request: null,
    result: null,
  }))

  for (const item of resolutions.value) {
    item.state = 'loading'
    item.summary = `正在按${snapshot.accessMode === 'authenticated' ? '登录' : '匿名'}身份获取实际媒体流`
    try {
      const streams = await videoApi.getStreams(snapshot.videoId, item.part.id, snapshot.accessMode)
      if (streams.partId !== item.part.id || streams.accessModeUsed !== snapshot.accessMode) {
        throw new ApiError({
          code: 'STREAM_CONTEXT_MISMATCH',
          message: '媒体流身份或分 P 与请求不一致',
          action: '重新解析视频后再试',
        })
      }
      const prepared = requestFor(item.part, streams, snapshot)
      item.request = prepared.request
      item.summary = requestSummary(item.request, streams, prepared.fallback)
      item.state = 'ready'
    } catch (reason) {
      item.summary = toApiError(reason).message
      item.state = 'error'
    }
  }

  if (resolutions.value.some((item) => item.state === 'error' || item.request === null)) {
    formError.value = new ApiError({
      code: 'BATCH_STREAM_RESOLUTION_FAILED',
      message: '部分分 P 无法形成安全下载方案，尚未创建任何任务。',
      action: '查看下方失败分 P；可移除后重试，或重新解析对应分 P。',
      status: 422,
    })
    submitting.value = false
    return
  }

  try {
    const requests = resolutions.value.map((item) => item.request as CreateDownloadRequest)
    const result = await jobs.createDownloadBatch({ downloads: requests })
    if (result.items.length !== resolutions.value.length) {
      throw new ApiError({
        code: 'BATCH_RESPONSE_MISMATCH',
        message: '批量任务响应数量与所选分 P 不一致',
        action: '前往任务中心核对已创建任务，避免立即重复提交',
      })
    }
    result.items.forEach((item, index) => {
      resolutions.value[index]!.result = item
    })
    emit('created', result.items.map((item) => item.job.id))
    ElNotification.success({
      title: '批量下载已处理',
      message: `新建 ${result.createdCount} 个，复用 ${result.reusedCount} 个；结果顺序与所选分 P 一致。`,
    })
  } catch (reason) {
    formError.value = toApiError(reason)
  } finally {
    submitting.value = false
  }
}

async function viewJobs(): Promise<void> {
  close()
  await router.push({ name: 'jobs' })
}
</script>

<template>
  <el-drawer
    :modelValue="modelValue"
    :direction="isMobile ? 'btt' : 'rtl'"
    :size="isMobile ? '92%' : '640px'"
    :closeOnClickModal="!submitting"
    :beforeClose="close"
    class="batch-download-drawer"
    data-testid="batch-download-drawer"
    @update:modelValue="$emit('update:modelValue', $event)"
  >
    <template #header>
      <div class="drawer-title"><span><Files /></span><div><h2>多 P 批量下载</h2><p>一次选择 2–20 个分 P，逐项解析后原子创建任务</p></div></div>
    </template>

    <div class="batch-content">
      <el-alert
        v-if="video.parts.length > 20"
        type="info"
        :closable="false"
        show-icon
        title="该视频超过 20 个分 P"
        description="单批最多处理 20 个；可搜索并选择任意分 P，完成后继续创建下一批。"
      />

      <section class="form-section part-section">
        <div class="section-heading"><div><h3>选择分 P</h3><p>已选 {{ selectedPartIds.length }} / 20</p></div><div><el-button text :disabled="submitting" @click="selectVisible">选择当前结果</el-button><el-button text :disabled="submitting" @click="clearSelection">清空</el-button></div></div>
        <el-input v-model="search" clearable :disabled="submitting" :prefix-icon="Search" placeholder="搜索 P 号或标题" />
        <el-checkbox-group v-model="selectedPartIds" :max="20" :disabled="submitting" class="part-list" aria-label="批量下载分 P">
          <el-checkbox v-for="part in filteredParts" :key="part.id" :value="part.id">
            <span><strong>P{{ part.pageNumber }}</strong><small>{{ part.title }}</small></span>
          </el-checkbox>
        </el-checkbox-group>
        <el-empty v-if="!filteredParts.length" :image-size="64" description="没有匹配的分 P" />
        <p class="field-note"><el-icon><InfoFilled /></el-icon>批量接口会先校验全部分 P；任一请求不合法时不会只创建一部分任务。</p>
      </section>

      <section class="form-section">
        <h3>共享下载策略</h3>
        <div class="preset-grid" role="radiogroup" aria-label="批量下载预设">
          <button v-for="item in presets" :key="item.value" type="button" :disabled="submitting" :aria-pressed="batchPreset === item.value" :class="{ active: batchPreset === item.value }" @click="batchPreset = item.value">
            <span class="option-check"><Check /></span><span><strong>{{ item.label }}</strong><small>{{ item.note }}</small></span>
          </button>
        </div>
        <div v-if="batchPreset !== 'audio_only' && batchPreset !== 'best_compatibility'" class="container-row">
          <span>输出容器</span>
          <el-radio-group v-model="form.container" :disabled="submitting"><el-radio-button value="mp4">MP4</el-radio-button><el-radio-button value="mkv">MKV</el-radio-button></el-radio-group>
        </div>
        <p class="field-note"><el-icon><InfoFilled /></el-icon>每个分 P 独立选择对应流；最佳兼容在缺少 H.264/AAC 时自动转码，不会把其他分 P 的流 ID 错用到当前分 P。</p>
      </section>

      <section class="form-section">
        <h3>随附内容</h3>
        <div class="switch-list">
          <label><span><strong>公开字幕</strong><small>存在时保存原始字幕</small></span><el-switch v-model="form.includeSubtitle" :disabled="submitting" /></label>
          <label><span><strong>弹幕 XML</strong><small>保存公开弹幕；无内容时仍以安全校验结果为准</small></span><el-switch v-model="form.includeDanmaku" :disabled="submitting" /></label>
          <label><span><strong>视频封面</strong><small>每个任务保存封面</small></span><el-switch v-model="form.includeCover" :disabled="submitting" /></label>
          <label><span><strong>元数据 JSON</strong><small>保存脱敏的结构化信息</small></span><el-switch v-model="form.includeMetadata" :disabled="submitting" /></label>
        </div>
      </section>

      <section class="form-section">
        <h3>文件名模板</h3>
        <el-input v-model="form.filenameTemplate" :disabled="submitting" maxlength="180" show-word-limit @input="validateFilename" />
        <p v-if="filenameError" class="field-error">{{ filenameError }}</p>
        <p v-else class="field-note">建议保留 <code>{page}</code> 或 <code>{part}</code>，便于区分不同分 P。</p>
      </section>

      <section class="form-section compact-section">
        <label class="switch-row"><span><strong>复用完整的已有任务或产物</strong><small>伴随产物缺失或失败时不会视为完整复用</small></span><el-switch v-model="form.reuseExisting" :disabled="submitting" /></label>
        <label class="switch-row"><span><strong>完成后清理临时文件</strong><small>不影响最终产物</small></span><el-switch v-model="form.cleanupTemporary" :disabled="submitting" /></label>
      </section>

      <section v-if="resolutions.length" class="resolution-section" aria-live="polite">
        <div class="section-heading"><div><h3>逐 P 解析结果</h3><p>{{ resolvedCount }} / {{ resolutions.length }}</p></div></div>
        <article v-for="item in resolutions" :key="item.part.id" :class="`is-${item.state}`">
          <span>P{{ item.part.pageNumber }}</span>
          <div><strong>{{ item.part.title }}</strong><small>{{ item.summary }}</small></div>
          <el-tag v-if="item.result" :type="item.result.reused ? 'info' : 'success'" effect="plain">{{ item.result.reused ? '已复用' : '已新建' }}</el-tag>
          <el-tag v-else-if="item.state === 'error'" type="danger" effect="plain">失败</el-tag>
          <el-tag v-else-if="item.state === 'ready'" type="success" effect="plain">已就绪</el-tag>
          <el-tag v-else type="info" effect="plain">{{ item.state === 'loading' ? '解析中' : '等待' }}</el-tag>
        </article>
      </section>

      <RequestError v-if="formError" :error="formError" />
      <div class="privacy-note"><el-icon><VideoCamera /></el-icon><p>每个任务只保存流规格和本次身份策略，不保存 Cookie 原文或长期媒体地址。</p></div>
    </div>

    <template #footer>
      <div v-if="completedResults.length" class="drawer-actions"><el-button @click="close">留在详情页</el-button><el-button type="primary" data-testid="view-batch-jobs" @click="viewJobs">前往任务中心</el-button></div>
      <div v-else class="drawer-actions"><el-button :disabled="submitting" @click="close">取消</el-button><el-button type="primary" :loading="submitting" :disabled="!canSubmit" data-testid="create-download-batch" @click="submit">校验并创建 {{ selectedPartIds.length }} 个任务</el-button></div>
    </template>
  </el-drawer>
</template>

<style scoped>
.drawer-title { display: flex; align-items: center; gap: 12px; color: var(--text-primary); }
.drawer-title > span { display: grid; place-items: center; width: 40px; height: 40px; border-radius: 12px; background: var(--brand-soft); color: var(--brand); }
.drawer-title svg { width: 20px; }.drawer-title h2, .drawer-title p { margin: 0; }.drawer-title h2 { font-size: 18px; }.drawer-title p { margin-top: 3px; color: var(--text-tertiary); font-size: 12px; }
.batch-content { display: grid; gap: 20px; }
.form-section { padding-bottom: 20px; border-bottom: 1px solid var(--line-soft); }.form-section h3 { margin: 0 0 12px; font-size: 14px; }.compact-section { display: grid; gap: 14px; padding-bottom: 0; border-bottom: 0; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 11px; }.section-heading h3, .section-heading p { margin: 0; }.section-heading p { margin-top: 3px; color: var(--text-tertiary); font-size: 11px; }.section-heading > div:last-child { display: flex; }
.part-list { display: grid; max-height: 260px; margin-top: 10px; overflow-y: auto; border: 1px solid var(--line-soft); border-radius: 12px; }
.part-list :deep(.el-checkbox) { width: 100%; min-height: 48px; height: auto; margin: 0; padding: 8px 12px; border-bottom: 1px solid var(--line-soft); }.part-list :deep(.el-checkbox:last-child) { border-bottom: 0; }.part-list :deep(.el-checkbox__label) { min-width: 0; }.part-list span { display: flex; align-items: center; gap: 10px; min-width: 0; }.part-list strong { flex: 0 0 38px; }.part-list small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.preset-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }.preset-grid button { display: flex; align-items: center; gap: 9px; min-height: 66px; padding: 11px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); color: var(--text-primary); text-align: left; cursor: pointer; }.preset-grid button.active { border-color: var(--brand); background: var(--brand-soft); }.preset-grid strong, .preset-grid small { display: block; }.preset-grid strong { font-size: 12px; }.preset-grid small { margin-top: 3px; color: var(--text-tertiary); font-size: 10px; line-height: 1.35; }
.preset-grid button:disabled { cursor: wait; opacity: .72; }
.option-check { display: grid; place-items: center; flex: 0 0 auto; width: 18px; height: 18px; border: 1px solid var(--line); border-radius: 50%; color: transparent; }.active .option-check { border-color: var(--brand); background: var(--brand); color: white; }.option-check svg { width: 11px; }
.container-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 13px; }.container-row > span { font-size: 12px; font-weight: 650; }
.switch-list { display: grid; gap: 14px; }.switch-list label, .switch-row { display: flex; align-items: center; justify-content: space-between; gap: 20px; }.switch-list strong, .switch-list small, .switch-row strong, .switch-row small { display: block; }.switch-list strong, .switch-row strong { font-size: 12px; }.switch-list small, .switch-row small { margin-top: 3px; color: var(--text-tertiary); font-size: 10px; }
.field-note { display: flex; align-items: flex-start; gap: 6px; margin: 9px 0 0; color: var(--text-tertiary); font-size: 11px; line-height: 1.55; }.field-note code { color: var(--text-secondary); }.field-error { margin: 7px 0 0; color: var(--danger); font-size: 12px; }
.resolution-section { display: grid; gap: 8px; }.resolution-section article { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 10px; padding: 11px; border: 1px solid var(--line-soft); border-radius: 11px; }.resolution-section article > span { display: grid; place-items: center; width: 40px; min-height: 36px; border-radius: 9px; background: var(--surface-muted); font-size: 11px; font-weight: 750; }.resolution-section strong, .resolution-section small { display: block; overflow-wrap: anywhere; }.resolution-section strong { font-size: 12px; }.resolution-section small { margin-top: 3px; color: var(--text-tertiary); font-size: 10px; }.resolution-section .is-error { border-color: #efb8b2; background: #fff7f6; }
.privacy-note { display: flex; gap: 9px; padding: 12px; border-radius: 11px; background: var(--brand-soft); color: var(--brand); }.privacy-note p { margin: 0; font-size: 11px; line-height: 1.55; }.drawer-actions { display: flex; justify-content: flex-end; gap: 8px; }
@media (max-width: 767px) {
  .preset-grid { grid-template-columns: 1fr; }.part-list { max-height: 220px; }.section-heading { align-items: flex-start; }.section-heading > div:last-child { flex-wrap: wrap; justify-content: flex-end; }
  .drawer-actions { display: grid; grid-template-columns: 1fr 2fr; }.drawer-actions .el-button { min-height: 48px; margin: 0; }
  .resolution-section article { grid-template-columns: auto minmax(0, 1fr); }.resolution-section article > .el-tag { grid-column: 2; justify-self: start; }
}
</style>
