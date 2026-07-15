<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElNotification } from 'element-plus'
import { Check, Cpu, MagicStick, Warning } from '@element-plus/icons-vue'
import RequestError from '@/components/RequestError.vue'
import { settingsApi } from '@/api'
import { useMobile } from '@/composables/useMobile'
import { useAnalysesStore } from '@/stores/analyses'
import { useJobsStore } from '@/stores/jobs'
import type { AnalysisCapability, AnalysisFeature, CreateAnalysisRequest, VideoDetail, VideoPart } from '@/types/api'

const props = defineProps<{
  modelValue: boolean
  video: VideoDetail
  part: VideoPart
  accessMode: 'anonymous' | 'authenticated'
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  created: [jobId: string]
}>()

const jobs = useJobsStore()
const analyses = useAnalysesStore()
const { isMobile } = useMobile()
const submitting = ref(false)
const requestError = ref<Error | null>(null)
const selected = ref<AnalysisFeature[]>(['metadata'])
const language = ref('zh-CN')
const asrModel = ref('small')
const ocrResolution = ref<'economy' | 'balanced' | 'detail'>('balanced')

const options: Array<{ value: AnalysisFeature; title: string; level: string; time: string; description: string; needsMedia: boolean }> = [
  { value: 'metadata', title: '基础内容概览', level: 'L0', time: '通常数秒', description: '元数据、标签、分 P、公开统计与字幕可用性。', needsMedia: false },
  { value: 'media', title: '媒体技术分析', level: 'L1', time: '约 1–5 分钟', description: '容器、编码、色彩、帧率、码率与关键帧间隔。', needsMedia: true },
  { value: 'audio', title: '音频技术与启发式区段', level: 'L1', time: '约 1–5 分钟', description: '响度、峰值、静音、相对频谱，以及明确标注局限的语音/音乐粗分类。', needsMedia: true },
  { value: 'subtitles', title: '公开字幕', level: 'L1', time: '通常数秒', description: '优先获取平台公开字幕，保留语言、来源和时间戳。', needsMedia: false },
  { value: 'asr', title: '语音转写 ASR', level: 'L2', time: '取决于视频时长', description: '无公开字幕时使用本地语音模型，结果包含置信度。', needsMedia: true },
  { value: 'ocr', title: '画面文字 OCR', level: 'L2', time: '资源占用较高', description: '提取硬字幕、片头片尾与关键画面文字。', needsMedia: true },
  { value: 'scenes', title: '镜头与关键帧', level: 'L2', time: '约视频时长 0.2–1 倍', description: '镜头切分、场景密度、关键帧与时间线。', needsMedia: true },
  { value: 'summary', title: '内容摘要', level: 'L3', time: '通常数秒', description: '自动收集元数据、公开字幕及历史 ASR/OCR/关键帧；证据不足时明确降级。', needsMedia: false },
]

const selectedOptions = computed(() => options.filter((option) => selected.value.includes(option.value)))
const needsMedia = computed(() => selectedOptions.value.some((option) => option.needsMedia))

watch(
  () => props.modelValue,
  async (open) => {
    if (!open) return
    void analyses.loadCapabilities()
    try {
      const settings = await settingsApi.get()
      language.value = settings.analysis.language
      asrModel.value = settings.analysis.asrModel
    } catch {
      // Keep safe local defaults when preferences are temporarily unavailable.
    }
  },
)

function capabilityFor(feature: AnalysisFeature): AnalysisCapability | undefined {
  const canonical = feature === 'metadata' ? 'basic' : feature
  return analyses.capabilities.find((item) => item.feature === canonical)
}

function canSelect(feature: AnalysisFeature): boolean {
  return capabilityFor(feature)?.available !== false
}

function toggle(feature: AnalysisFeature): void {
  const capability = capabilityFor(feature)
  if (capability?.available === false) {
    ElMessage.warning(capability.action || capability.message)
    return
  }
  if (selected.value.includes(feature)) selected.value = selected.value.filter((item) => item !== feature)
  else selected.value = [...selected.value, feature]
}

function close(): void {
  if (!submitting.value) emit('update:modelValue', false)
}

async function submit(): Promise<void> {
  if (!selected.value.length) {
    ElMessage.warning('请至少选择一项分析能力')
    return
  }
  const unavailable = selected.value
    .map((feature) => capabilityFor(feature))
    .find((capability) => capability?.available === false)
  if (unavailable) {
    ElMessage.warning(unavailable.action || unavailable.message)
    return
  }
  submitting.value = true
  requestError.value = null
  const request: CreateAnalysisRequest = {
    videoId: props.video.id,
    partIds: [props.part.id],
    features: selected.value,
    language: language.value,
    accessMode: props.accessMode,
    asrModel: asrModel.value,
    ocrResolution: ocrResolution.value,
  }
  try {
    const job = await jobs.createAnalysis(request)
    if (job.reused) {
      ElNotification.info({ title: '已复用现有分析', message: '相同视频、分 P 与分析参数的任务已经存在。' })
    } else {
      ElNotification.success({ title: '分析任务已创建', message: '每项能力独立执行，单项失败不会移除已完成结果。' })
    }
    emit('created', job.id)
    emit('update:modelValue', false)
  } catch (reason) {
    requestError.value = reason instanceof Error ? reason : new Error('创建分析任务失败')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-drawer
    :modelValue="modelValue"
    :direction="isMobile ? 'btt' : 'rtl'"
    :size="isMobile ? '92%' : '560px'"
    :closeOnClickModal="!submitting"
    :beforeClose="close"
    data-testid="analysis-config-drawer"
    @update:modelValue="$emit('update:modelValue', $event)"
  >
    <template #header>
      <div class="drawer-title"><span><MagicStick /></span><div><h2>配置分析任务</h2><p>{{ part.title }}</p></div></div>
    </template>

    <div class="analysis-content">
      <div class="analysis-options">
        <button v-for="option in options" :key="option.value" type="button" :disabled="!canSelect(option.value)" :class="{ selected: selected.includes(option.value) }" @click="toggle(option.value)">
          <span class="check"><Check /></span>
          <span class="option-copy"><span class="title-row"><strong>{{ option.title }}</strong><el-tag size="small" effect="plain">{{ option.level }}</el-tag></span><small>{{ option.description }}</small><em v-if="capabilityFor(option.value)?.available === false" class="unavailable">{{ capabilityFor(option.value)?.message }}<template v-if="capabilityFor(option.value)?.action"> · {{ capabilityFor(option.value)?.action }}</template></em><em v-else>{{ option.time }} · {{ option.needsMedia ? '需要媒体' : '无需下载媒体' }}</em></span>
        </button>
      </div>

      <section v-if="selected.includes('asr') || selected.includes('ocr')" class="model-options">
        <h3><el-icon><Cpu /></el-icon>模型参数</h3>
        <div class="model-grid">
          <label><span>识别语言</span><el-select v-model="language"><el-option label="中文（简体）" value="zh-CN" /><el-option label="自动检测" value="auto" /><el-option label="英语" value="en" /><el-option label="日语" value="ja" /></el-select></label>
          <label v-if="selected.includes('asr')"><span>ASR 模型</span><el-select v-model="asrModel"><el-option label="Tiny（最快）" value="tiny" /><el-option label="Base" value="base" /><el-option label="Small（推荐）" value="small" /><el-option label="Medium" value="medium" /><el-option label="Large v3（最精细）" value="large-v3" /></el-select></label>
          <label v-if="selected.includes('ocr')"><span>OCR 采样质量</span><el-select v-model="ocrResolution"><el-option label="经济" value="economy" /><el-option label="均衡（推荐）" value="balanced" /><el-option label="细节优先" value="detail" /></el-select></label>
        </div>
      </section>

      <div v-if="needsMedia" class="media-note"><el-icon><Warning /></el-icon><p>分析将优先使用足够完成任务的较低码率媒体，不会因登录态拥有高画质而自动下载最高规格。OCR 选择“细节优先”会增加流量和磁盘占用。</p></div>
      <div v-if="selected.includes('summary')" class="ai-note"><strong>自动分析结果可能存在误差</strong><p>报告将记录模型、版本、生成时间与输入来源；关键结论应结合时间戳、转写或关键帧证据复核。</p></div>

      <RequestError v-if="requestError && 'code' in requestError" :error="requestError as import('@/api/errors').ApiError" />
    </div>

    <template #footer>
      <div class="drawer-actions"><span>已选 {{ selected.length }} 项</span><el-button :disabled="submitting" @click="close">取消</el-button><el-button type="primary" :loading="submitting" data-testid="create-analysis-job" @click="submit">创建分析任务</el-button></div>
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
.analysis-content { display: grid; gap: 20px; }
.analysis-options { display: grid; gap: 9px; }
.analysis-options button { display: flex; align-items: flex-start; gap: 11px; min-height: 91px; padding: 14px; border: 1px solid var(--line); border-radius: 13px; background: var(--surface); color: var(--text-primary); text-align: left; cursor: pointer; }
.analysis-options button.selected { border-color: var(--brand); background: var(--brand-soft); }
.analysis-options button:disabled { cursor: not-allowed; opacity: .68; }
.check { display: grid; place-items: center; flex: 0 0 auto; width: 21px; height: 21px; margin-top: 1px; border: 1px solid var(--line); border-radius: 6px; color: transparent; }
.selected .check { border-color: var(--brand); background: var(--brand); color: white; }
.check svg { width: 13px; }
.option-copy { min-width: 0; }
.title-row { display: flex; align-items: center; gap: 8px; }
.option-copy small, .option-copy em { display: block; }
.option-copy small { margin-top: 5px; color: var(--text-secondary); font-size: 11px; line-height: 1.5; }
.option-copy em { margin-top: 7px; color: var(--text-tertiary); font-size: 10px; font-style: normal; }
.option-copy em.unavailable { color: var(--danger); }
.model-options { padding: 16px; border-radius: 13px; background: var(--surface-muted); }
.model-options h3 { display: flex; align-items: center; gap: 7px; margin: 0 0 13px; font-size: 13px; }
.model-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.model-grid label { display: grid; gap: 6px; }
.model-grid label > span { color: var(--text-secondary); font-size: 11px; font-weight: 650; }
.media-note, .ai-note { padding: 13px; border-radius: 11px; font-size: 11px; line-height: 1.6; }
.media-note { display: flex; align-items: flex-start; gap: 9px; background: #fff6e9; color: #95521b; }
.media-note p, .ai-note p { margin: 0; }
.ai-note { background: var(--brand-soft); color: var(--brand); }
.ai-note p { margin-top: 4px; color: var(--text-secondary); }
.drawer-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; }
.drawer-actions > span { margin-right: auto; color: var(--text-secondary); font-size: 12px; }
@media (max-width: 767px) {
  .model-grid { grid-template-columns: 1fr; }
  .drawer-actions { display: grid; grid-template-columns: 1fr 1fr; }
  .drawer-actions > span { grid-column: 1 / -1; }
  .drawer-actions .el-button { min-height: 48px; margin: 0; }
}
</style>
