<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElNotification } from 'element-plus'
import { Box, Check, Document, InfoFilled, VideoPlay } from '@element-plus/icons-vue'
import RequestError from '@/components/RequestError.vue'
import { useMobile } from '@/composables/useMobile'
import { useJobsStore } from '@/stores/jobs'
import type {
  CreateDownloadRequest,
  DownloadPreset,
  MediaStream,
  OutputContainer,
  VideoDetail,
  VideoPart,
} from '@/types/api'
import { formatBitrate, formatBytes } from '@/utils/format'
import { copyCompatibilityIssue, isBestCompatibilitySource } from '@/utils/mediaCompatibility'

const props = defineProps<{
  modelValue: boolean
  video: VideoDetail
  part: VideoPart
  videoStream: MediaStream | null
  audioStream: MediaStream | null
  accessMode: 'anonymous' | 'authenticated'
  preset?: DownloadPreset
  defaultFilenameTemplate?: string
  defaultContainer?: 'mp4' | 'mkv'
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  created: [jobId: string]
}>()

const jobs = useJobsStore()
const { isMobile } = useMobile()
const submitting = ref(false)
const formError = ref<Error | null>(null)
const filenameError = ref('')

const form = reactive({
  container: 'mp4' as OutputContainer,
  processingMode: 'copy' as 'copy' | 'transcode',
  includeSubtitle: true,
  includeDanmaku: false,
  includeCover: true,
  includeMetadata: true,
  filenameTemplate: '{title} - P{page}',
  cleanupTemporary: true,
  reuseExisting: true,
})

const audioOnly = computed(() => !props.videoStream)
const containers = computed<Array<{ value: OutputContainer; label: string; note: string }>>(() =>
  audioOnly.value
    ? [
        { value: 'm4a', label: 'M4A', note: 'AAC 原始封装' },
        { value: 'mp3', label: 'MP3', note: '广泛兼容，需转码' },
        { value: 'flac', label: 'FLAC', note: '无损音频，需转码且体积较大' },
      ]
    : [
        { value: 'mp4', label: 'MP4', note: '设备兼容优先' },
        { value: 'mkv', label: 'MKV', note: '轨道与编码兼容更宽' },
      ],
)

const estimatedTotal = computed(() => (props.videoStream?.estimatedSize ?? 0) + (props.audioStream?.estimatedSize ?? 0))
const copyIssue = computed(() => copyCompatibilityIssue(form.container, props.videoStream, props.audioStream))
const copyCompatible = computed(() => copyIssue.value === null)

function applySafeProcessingMode(): void {
  if (form.container === 'mp3' || form.container === 'flac' || !copyCompatible.value) {
    form.processingMode = 'transcode'
  }
}

watch(audioOnly, (value) => {
  form.container = value ? 'm4a' : 'mp4'
  form.processingMode = 'copy'
}, { immediate: true })

watch(() => form.container, (container) => {
  if (container === 'mp3' || container === 'flac') form.processingMode = 'transcode'
  else applySafeProcessingMode()
})

watch(
  () => [props.videoStream?.id, props.audioStream?.id],
  applySafeProcessingMode,
)

watch(() => props.modelValue, (open) => {
  if (!open) return
  form.filenameTemplate = props.defaultFilenameTemplate?.trim() || '{title} - P{page}'
  form.container = audioOnly.value
    ? 'm4a'
    : props.preset === 'best_compatibility'
      ? 'mp4'
      : (props.defaultContainer ?? 'mp4')
  form.processingMode = props.preset === 'best_compatibility'
    && !isBestCompatibilitySource(props.videoStream, props.audioStream)
    ? 'transcode'
    : 'copy'
  applySafeProcessingMode()
  filenameError.value = ''
  form.reuseExisting = true
})

function close(): void {
  if (!submitting.value) emit('update:modelValue', false)
}

function validateFilename(): boolean {
  const value = form.filenameTemplate.trim()
  if (!value) filenameError.value = '请输入文件名模板'
  else if (value.length > 180) filenameError.value = '文件名模板不能超过 180 个字符'
  else if (/[<>:"/\\|?*\u0000-\u001F]/.test(value)) filenameError.value = '文件名不能包含 < > : " / \\ | ? * 或控制字符'
  else filenameError.value = ''
  return !filenameError.value
}

async function submit(): Promise<void> {
  if (!validateFilename()) return
  if (!props.videoStream && !props.audioStream) {
    ElMessage.warning('请至少选择一个视频或音频流')
    return
  }
  if (form.processingMode === 'copy' && copyIssue.value) {
    ElMessage.warning(`${copyIssue.value}，请选择兼容转码或更换封装格式`)
    return
  }
  submitting.value = true
  formError.value = null
  const request: CreateDownloadRequest = {
    videoId: props.video.id,
    partId: props.part.id,
    videoStreamId: props.videoStream?.id ?? null,
    audioStreamId: props.audioStream?.id ?? 'none',
    container: form.container,
    processingMode: form.processingMode,
    accessMode: props.accessMode,
    includeSubtitle: form.includeSubtitle,
    includeDanmaku: form.includeDanmaku,
    includeCover: form.includeCover,
    includeMetadata: form.includeMetadata,
    cleanupTemporary: form.cleanupTemporary,
    filename: form.filenameTemplate.trim(),
    reuseExisting: form.reuseExisting,
  }
  try {
    const result = await jobs.createDownload(request)
    if (result.reused) {
      ElNotification.info({ title: '已复用现有任务', message: '相同规格的任务或完整产物已经存在，未重复入队。' })
    } else {
      ElNotification.success({ title: '下载任务已创建', message: '任务开始前会重新获取并验证媒体地址。' })
    }
    emit('created', result.job.id)
    emit('update:modelValue', false)
  } catch (reason) {
    formError.value = reason instanceof Error ? reason : new Error('创建任务失败')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-drawer
    :modelValue="modelValue"
    :direction="isMobile ? 'btt' : 'rtl'"
    :size="isMobile ? '88%' : '520px'"
    :closeOnClickModal="!submitting"
    :beforeClose="close"
    class="download-drawer"
    data-testid="download-config-drawer"
    @update:modelValue="$emit('update:modelValue', $event)"
  >
    <template #header>
      <div class="drawer-title"><span><VideoPlay /></span><div><h2>配置下载任务</h2><p>{{ part.title }}</p></div></div>
    </template>

    <div class="config-content">
      <section class="selected-summary">
        <div><small>视频流</small><strong>{{ videoStream ? `${videoStream.qualityLabel} · ${videoStream.codec}` : '不下载视频' }}</strong><span v-if="videoStream">{{ videoStream.width }}×{{ videoStream.height }} · {{ formatBitrate(videoStream.bitrate) }}</span></div>
        <div><small>音频流</small><strong>{{ audioStream ? `${audioStream.codec} · ${formatBitrate(audioStream.bitrate)}` : '不附加音频' }}</strong><span>{{ audioStream ? `约 ${formatBytes(audioStream.estimatedSize)}` : '—' }}</span></div>
        <div><small>预计总量</small><strong>{{ formatBytes(estimatedTotal) }}</strong><span>任务开始前将重新校验</span></div>
      </section>

      <section class="form-section">
        <h3>输出格式</h3>
        <div class="container-options">
          <button v-for="item in containers" :key="item.value" type="button" :class="{ active: form.container === item.value }" @click="form.container = item.value">
            <span class="option-check"><Check /></span><span><strong>{{ item.label }}</strong><small>{{ item.note }}</small></span>
          </button>
        </div>
      </section>

      <section class="form-section">
        <h3>处理方式</h3>
        <el-radio-group v-model="form.processingMode">
          <el-radio-button value="copy" :disabled="!copyCompatible">无损封装</el-radio-button>
          <el-radio-button value="transcode">兼容转码</el-radio-button>
        </el-radio-group>
        <p class="field-note"><el-icon><InfoFilled /></el-icon>{{ copyIssue ? `${copyIssue}；当前必须转码或更换封装格式。` : form.processingMode === 'copy' ? '保留原始编码，速度快且无质量损失。' : '视频转为 H.264、音频转为 AAC；耗时更长且可能有质量损失。' }}</p>
      </section>

      <section class="form-section">
        <h3>随附内容</h3>
        <div class="switch-list">
          <label><span><strong>公开字幕</strong><small>存在时保存原始字幕文件</small></span><el-switch v-model="form.includeSubtitle" /></label>
          <label><span><strong>弹幕 XML</strong><small>保存当前分 P 的公开弹幕；内容按不可信文本处理</small></span><el-switch v-model="form.includeDanmaku" /></label>
          <label><span><strong>视频封面</strong><small>保存原始封面图片</small></span><el-switch v-model="form.includeCover" /></label>
          <label><span><strong>元数据 JSON</strong><small>保存脱敏的结构化视频信息</small></span><el-switch v-model="form.includeMetadata" /></label>
        </div>
      </section>

      <section class="form-section">
        <h3>文件名</h3>
        <el-input v-model="form.filenameTemplate" maxlength="180" show-word-limit @input="validateFilename">
          <template #prefix><el-icon><Document /></el-icon></template>
        </el-input>
        <p v-if="filenameError" class="field-error">{{ filenameError }}</p>
        <p v-else class="field-note">可用变量：<code>{title}</code>、<code>{bvid}</code>、<code>{page}</code>、<code>{part}</code>、<code>{quality}</code></p>
      </section>

      <section class="form-section compact-section">
        <label class="cleanup-row"><span><strong>复用已有任务或产物</strong><small>相同规格已存在时不重复下载</small></span><el-switch v-model="form.reuseExisting" /></label>
        <label class="cleanup-row"><span><strong>完成后清理临时文件</strong><small>仅删除音视频分片，不影响最终产物</small></span><el-switch v-model="form.cleanupTemporary" /></label>
      </section>

      <RequestError v-if="formError && 'code' in formError" :error="formError as import('@/api/errors').ApiError" />

      <div class="privacy-note"><el-icon><Box /></el-icon><p>任务仅保存所选流规格与身份策略，不保存 Cookie 原文或长期媒体地址。</p></div>
    </div>

    <template #footer>
      <div class="drawer-actions"><el-button :disabled="submitting" @click="close">取消</el-button><el-button type="primary" :loading="submitting" data-testid="create-download-job" @click="submit">创建下载任务</el-button></div>
    </template>
  </el-drawer>
</template>

<style scoped>
.drawer-title { display: flex; align-items: center; gap: 12px; color: var(--text-primary); }
.drawer-title > span { display: grid; place-items: center; width: 40px; height: 40px; border-radius: 12px; background: var(--brand-soft); color: var(--brand); }
.drawer-title svg { width: 20px; }
.drawer-title h2, .drawer-title p { margin: 0; }
.drawer-title h2 { font-size: 18px; }
.drawer-title p { margin-top: 3px; color: var(--text-tertiary); font-size: 12px; }
.config-content { display: grid; gap: 22px; }
.selected-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; padding: 13px; border-radius: 13px; background: var(--surface-muted); }
.selected-summary div { min-width: 0; }
.selected-summary small, .selected-summary strong, .selected-summary span { display: block; overflow-wrap: anywhere; }
.selected-summary small { color: var(--text-tertiary); font-size: 10px; }
.selected-summary strong { margin-top: 5px; font-size: 12px; }
.selected-summary span { margin-top: 3px; color: var(--text-secondary); font-size: 10px; }
.form-section { padding-bottom: 21px; border-bottom: 1px solid var(--line-soft); }
.form-section h3 { margin: 0 0 12px; font-size: 14px; }
.compact-section { padding-bottom: 0; border-bottom: 0; }
.container-options { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.container-options button { display: flex; align-items: center; gap: 8px; min-height: 62px; padding: 10px; border: 1px solid var(--line); border-radius: 11px; background: var(--surface); color: var(--text-primary); text-align: left; cursor: pointer; }
.container-options button.active { border-color: var(--brand); background: var(--brand-soft); }
.option-check { display: grid; place-items: center; flex: 0 0 auto; width: 17px; height: 17px; border: 1px solid var(--line); border-radius: 50%; color: transparent; }
.active .option-check { border-color: var(--brand); background: var(--brand); color: white; }
.option-check svg { width: 10px; }
.container-options strong, .container-options small { display: block; }
.container-options strong { font-size: 12px; }
.container-options small { margin-top: 3px; color: var(--text-tertiary); font-size: 9px; }
.field-note { display: flex; align-items: flex-start; gap: 6px; margin: 9px 0 0; color: var(--text-tertiary); font-size: 11px; line-height: 1.55; }
.field-note code { color: var(--text-secondary); }
.field-error { margin: 7px 0 0; color: var(--danger); font-size: 12px; }
.switch-list { display: grid; gap: 14px; }
.switch-list label, .cleanup-row { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.compact-section { display: grid; gap: 14px; }
.switch-list strong, .switch-list small, .cleanup-row strong, .cleanup-row small { display: block; }
.switch-list strong, .cleanup-row strong { font-size: 12px; }
.switch-list small, .cleanup-row small { margin-top: 3px; color: var(--text-tertiary); font-size: 10px; }
.privacy-note { display: flex; gap: 9px; padding: 12px; border-radius: 11px; background: var(--brand-soft); color: var(--brand); }
.privacy-note p { margin: 0; font-size: 11px; line-height: 1.55; }
.drawer-actions { display: flex; justify-content: flex-end; gap: 8px; }
@media (max-width: 767px) {
  .selected-summary { grid-template-columns: 1fr 1fr; }
  .selected-summary div:last-child { grid-column: 1 / -1; }
  .drawer-actions { display: grid; grid-template-columns: 1fr 2fr; }
  .drawer-actions .el-button { min-height: 48px; margin: 0; }
}
</style>
