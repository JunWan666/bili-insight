<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Delete, EditPen, Plus } from '@element-plus/icons-vue'
import { useMobile } from '@/composables/useMobile'
import type {
  AnalysisTranscript,
  EditTranscriptRequest,
  TranscriptEditSegmentRequest,
} from '@/types/api'
import { formatDuration } from '@/utils/format'

const props = defineProps<{
  modelValue: boolean
  transcript: AnalysisTranscript
  saving: boolean
  errorMessage: string | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  save: [request: EditTranscriptRequest]
}>()

interface EditableSegment extends TranscriptEditSegmentRequest {
  key: number
}

const { isMobile } = useMobile()
const segments = ref<EditableSegment[]>([])
let nextKey = 1

const validationMessage = computed(() => {
  if (!segments.value.length) return '至少保留一个文本片段。'
  if (segments.value.length > 10_000) return '在线编辑最多支持 10,000 个片段。'
  let characters = 0
  let previousStart: number | null = null
  for (const [index, segment] of segments.value.entries()) {
    const text = segment.text.trim()
    characters += text.length
    if (!Number.isFinite(segment.startSeconds) || !Number.isFinite(segment.endSeconds)) {
      return `第 ${index + 1} 段时间戳不是有效数字。`
    }
    if (segment.startSeconds < 0 || segment.endSeconds <= segment.startSeconds) {
      return `第 ${index + 1} 段结束时间必须晚于开始时间。`
    }
    if (segment.endSeconds > 604_800) return `第 ${index + 1} 段时间戳超过安全上限。`
    if (previousStart !== null && segment.startSeconds < previousStart) {
      return `第 ${index + 1} 段开始时间不能早于上一段。`
    }
    previousStart = segment.startSeconds
    if (!text) return `第 ${index + 1} 段文本不能为空。`
    if (text.length > 5_000) return `第 ${index + 1} 段文本超过 5,000 字。`
  }
  return characters > 2_000_000 ? '编辑后的总文本超过 2,000,000 字。' : null
})

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    nextKey = 1
    segments.value = props.transcript.segments.map((segment) => ({
      key: nextKey++,
      startSeconds: segment.startSeconds,
      endSeconds: segment.endSeconds,
      text: segment.text,
    }))
  },
  { immediate: true },
)

function addSegment(): void {
  const previous = segments.value.at(-1)
  const start = previous?.endSeconds ?? 0
  segments.value.push({ key: nextKey++, startSeconds: start, endSeconds: start + 2, text: '' })
}

function removeSegment(index: number): void {
  segments.value.splice(index, 1)
}

function close(): void {
  if (!props.saving) emit('update:modelValue', false)
}

function submit(): void {
  if (validationMessage.value) return
  emit('save', {
    segments: segments.value.map((segment) => ({
      startSeconds: segment.startSeconds,
      endSeconds: segment.endSeconds,
      text: segment.text.trim(),
    })),
  })
}
</script>

<template>
  <el-drawer
    :model-value="modelValue"
    :direction="isMobile ? 'btt' : 'rtl'"
    :size="isMobile ? '94%' : '680px'"
    :close-on-click-modal="!saving"
    :before-close="close"
    data-testid="transcript-edit-drawer"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <template #header>
      <div class="drawer-title">
        <span><EditPen /></span>
        <div>
          <h2>编辑时间轴文本</h2>
          <p>{{ transcript.language }} · 原来源 {{ transcript.source }} · {{ transcript.segments.length }} 段</p>
        </div>
      </div>
    </template>

    <div class="edit-content">
      <el-alert
        title="保存会创建人工编辑版本，原始识别记录和原始导出文件不会被覆盖。"
        type="info"
        :closable="false"
        show-icon
      />
      <el-alert
        v-if="errorMessage"
        :title="errorMessage"
        type="error"
        :closable="false"
        show-icon
      />
      <ol class="segment-list">
        <li v-for="(segment, index) in segments" :key="segment.key">
          <div class="segment-heading">
            <strong>片段 {{ index + 1 }}</strong>
            <span>{{ formatDuration(segment.startSeconds) }} – {{ formatDuration(segment.endSeconds) }}</span>
            <el-button
              :icon="Delete"
              text
              type="danger"
              :disabled="saving || segments.length === 1"
              :aria-label="`删除片段 ${index + 1}`"
              @click="removeSegment(index)"
            />
          </div>
          <div class="time-inputs">
            <label>
              <span>开始（秒）</span>
              <el-input-number v-model="segment.startSeconds" :min="0" :max="604800" :step="0.1" :precision="3" :disabled="saving" />
            </label>
            <label>
              <span>结束（秒）</span>
              <el-input-number v-model="segment.endSeconds" :min="0.001" :max="604800" :step="0.1" :precision="3" :disabled="saving" />
            </label>
          </div>
          <label class="text-input">
            <span>文本</span>
            <el-input v-model="segment.text" type="textarea" :rows="3" maxlength="5000" show-word-limit :disabled="saving" />
          </label>
        </li>
      </ol>
      <el-button :icon="Plus" plain :disabled="saving || segments.length >= 10000" @click="addSegment">添加片段</el-button>
    </div>

    <template #footer>
      <div class="drawer-actions">
        <p :class="{ invalid: validationMessage }">{{ validationMessage || `将发布 ${segments.length} 个 edited 来源片段及 SRT / VTT / TXT / JSON 导出。` }}</p>
        <el-button :disabled="saving" @click="close">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="Boolean(validationMessage)" data-testid="save-transcript-edit" @click="submit">保存人工编辑版本</el-button>
      </div>
    </template>
  </el-drawer>
</template>

<style scoped>
.drawer-title { display: flex; align-items: center; gap: 12px; color: var(--text-primary); }
.drawer-title > span { display: grid; place-items: center; width: 40px; height: 40px; border-radius: 12px; background: var(--brand-soft); color: var(--brand); }
.drawer-title svg { width: 20px; }
.drawer-title h2, .drawer-title p { margin: 0; }
.drawer-title h2 { font-size: 18px; }
.drawer-title p { margin-top: 3px; color: var(--text-tertiary); font-size: 11px; overflow-wrap: anywhere; }
.edit-content { display: grid; gap: 14px; }
.segment-list { display: grid; gap: 12px; margin: 0; padding: 0; list-style: none; }
.segment-list li { min-width: 0; padding: 14px; border: 1px solid var(--line-soft); border-radius: 12px; background: var(--surface); }
.segment-heading { display: flex; align-items: center; gap: 10px; }
.segment-heading strong { font-size: 12px; }
.segment-heading span { margin-right: auto; color: var(--text-tertiary); font-size: 10px; }
.segment-heading .el-button { min-width: 44px; min-height: 44px; margin: -10px -10px -10px 0; }
.time-inputs { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 10px; }
.time-inputs label, .text-input { display: grid; min-width: 0; gap: 6px; }
.time-inputs label > span, .text-input > span { color: var(--text-secondary); font-size: 10px; font-weight: 650; }
.time-inputs :deep(.el-input-number) { width: 100%; }
.text-input { margin-top: 10px; }
.drawer-actions { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: 8px; }
.drawer-actions p { min-width: 0; margin: 0; color: var(--text-secondary); font-size: 10px; line-height: 1.45; overflow-wrap: anywhere; }
.drawer-actions p.invalid { color: var(--danger); }
@media (max-width: 767px) {
  .time-inputs { grid-template-columns: 1fr; }
  .drawer-actions { grid-template-columns: 1fr 1fr; }
  .drawer-actions p { grid-column: 1 / -1; }
  .drawer-actions .el-button { min-height: 48px; margin: 0; }
}
</style>
