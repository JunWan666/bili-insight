<script setup lang="ts">
import { computed, watch } from 'vue'
import { MagicStick, Refresh, WarningFilled } from '@element-plus/icons-vue'
import AnalysisResultCard from './AnalysisResultCard.vue'
import RequestError from '@/components/RequestError.vue'
import { useAnalysesStore } from '@/stores/analyses'
import type { AnalysisCanonicalFeature, AnalysisRecord } from '@/types/api'
import { formatDate } from '@/utils/format'

const props = defineProps<{
  videoId: string
  partId: string
  kind: 'technical' | 'content'
}>()

const emit = defineEmits<{ create: [] }>()
const analyses = useAnalysesStore()

const featureOrder: Record<AnalysisCanonicalFeature, number> = {
  basic: 0,
  media: 1,
  audio: 2,
  scenes: 3,
  subtitles: 4,
  asr: 5,
  ocr: 6,
  summary: 7,
}

const supportedFeatures = computed<AnalysisCanonicalFeature[]>(() => (
  props.kind === 'technical'
    ? ['media', 'audio', 'scenes']
    : ['basic', 'subtitles', 'asr', 'ocr', 'summary']
))

const visibleRecords = computed(() => {
  const groups = new Map<AnalysisCanonicalFeature, AnalysisRecord[]>()
  for (const record of analyses.items) {
    if (!supportedFeatures.value.includes(record.feature)) continue
    const group = groups.get(record.feature) ?? []
    group.push(record)
    groups.set(record.feature, group)
  }

  const selected: AnalysisRecord[] = []
  for (const group of groups.values()) {
    group.sort((left, right) => Date.parse(right.updatedAt) - Date.parse(left.updatedAt))
    const latest = group[0]
    if (!latest) continue
    selected.push(latest)
    if (latest.status !== 'completed') {
      const previousSuccess = group.find((record) => record.status === 'completed')
      if (previousSuccess) selected.push(previousSuccess)
    }
  }
  return selected.sort((left, right) => (
    featureOrder[left.feature] - featureOrder[right.feature]
      || Date.parse(right.updatedAt) - Date.parse(left.updatedAt)
  ))
})

const unavailableCapabilities = computed(() => analyses.capabilities.filter((capability) => (
  supportedFeatures.value.includes(capability.feature) && !capability.available
)))

const resultSummary = computed(() => {
  const completed = visibleRecords.value.filter((record) => record.status === 'completed').length
  const failed = visibleRecords.value.filter((record) => record.status === 'failed').length
  const active = visibleRecords.value.filter((record) => record.status === 'running').length
  return { completed, failed, active }
})

function load(force = false): Promise<void> {
  return force
    ? analyses.refresh(props.videoId, props.partId)
    : analyses.load(props.videoId, props.partId)
}

watch(
  () => [props.videoId, props.partId],
  () => { void load() },
  { immediate: true },
)
</script>

<template>
  <section class="analysis-results" :data-testid="`analysis-results-${kind}`">
    <div class="results-heading">
      <div>
        <h3>{{ kind === 'technical' ? '已保存的技术分析' : '已保存的内容分析' }}</h3>
        <p>
          <template v-if="visibleRecords.length">
            {{ resultSummary.completed }} 项成功<template v-if="resultSummary.failed"> · {{ resultSummary.failed }} 项失败</template><template v-if="resultSummary.active"> · {{ resultSummary.active }} 项进行中</template>
          </template>
          <template v-else>结果按当前视频与分 P 独立加载，刷新不会重新运行分析。</template>
          <span v-if="analyses.refreshedAt"> · 查询于 {{ formatDate(analyses.refreshedAt) }}</span>
        </p>
      </div>
      <div class="heading-actions">
        <el-button :icon="Refresh" :loading="analyses.loading" data-testid="refresh-analysis-results" @click="load(true)">刷新结果</el-button>
        <el-button type="primary" :icon="MagicStick" @click="emit('create')">创建分析</el-button>
      </div>
    </div>

    <div v-if="unavailableCapabilities.length" class="capability-warnings" role="note">
      <el-icon><WarningFilled /></el-icon>
      <div>
        <strong>部分本地分析能力当前不可用</strong>
        <p v-for="capability in unavailableCapabilities" :key="`${capability.feature}-${capability.component}`">
          {{ capability.message }}<template v-if="capability.action">；{{ capability.action }}</template>
        </p>
      </div>
    </div>
    <p v-else-if="analyses.capabilitiesError" class="capability-query-error">能力状态暂时未能更新，不影响下方已保存结果。</p>

    <RequestError
      v-if="analyses.error"
      class="results-error"
      :error="analyses.error"
      :title="visibleRecords.length ? '刷新失败，正在显示上次成功加载的结果' : undefined"
      @retry="load(true)"
    />

    <div v-if="analyses.loading && !visibleRecords.length" class="results-loading" aria-label="正在加载分析结果"><el-skeleton :rows="6" animated /></div>
    <div v-else-if="visibleRecords.length" class="record-list">
      <AnalysisResultCard v-for="record in visibleRecords" :key="record.id" :record="record" @edited="load(true)" />
    </div>
    <el-empty v-else-if="!analyses.error" :image-size="88" :description="kind === 'technical' ? '当前分 P 还没有技术分析结果' : '当前分 P 还没有内容分析结果'">
      <el-button type="primary" plain @click="emit('create')">创建第一个分析任务</el-button>
    </el-empty>
  </section>
</template>

<style scoped>
.analysis-results { min-width: 0; margin-top: 28px; padding-top: 24px; border-top: 1px solid var(--line-soft); }
.results-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
.results-heading h3 { margin: 0; font-size: 17px; }
.results-heading p { margin: 6px 0 0; color: var(--text-secondary); font-size: 11px; line-height: 1.55; }
.heading-actions { display: flex; flex: 0 0 auto; gap: 8px; }
.heading-actions .el-button + .el-button { margin-left: 0; }
.capability-warnings { display: flex; align-items: flex-start; gap: 10px; margin-top: 16px; padding: 13px; border-radius: 11px; background: #fff6e9; color: #95521b; }
.capability-warnings > .el-icon { flex: 0 0 auto; margin-top: 2px; }
.capability-warnings strong { display: block; }
.capability-warnings p { margin: 4px 0 0; font-size: 11px; line-height: 1.55; }
.capability-query-error { margin: 14px 0 0; color: var(--text-tertiary); font-size: 11px; }
.results-error, .results-loading, .record-list { margin-top: 16px; }
.record-list { display: grid; gap: 14px; }
@media (max-width: 767px) {
  .analysis-results { margin-top: 22px; padding-top: 20px; }
  .results-heading { display: block; }
  .heading-actions { display: grid; grid-template-columns: 1fr 1fr; margin-top: 13px; }
  .heading-actions .el-button { min-width: 0; margin: 0; }
}
@media (max-width: 374px) {
  .heading-actions { grid-template-columns: 1fr; }
}
</style>
