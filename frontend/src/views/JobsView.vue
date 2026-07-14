<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  CircleCheck,
  CircleClose,
  Clock,
  Connection,
  Document,
  Download,
  Files,
  InfoFilled,
  MagicStick,
  MoreFilled,
  Refresh,
  RefreshLeft,
  VideoPause,
  VideoPlay,
  Warning,
} from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import RequestError from '@/components/RequestError.vue'
import { useMobile } from '@/composables/useMobile'
import { useJobsStore } from '@/stores/jobs'
import type { Job, JobStatus, JobType } from '@/types/api'
import { formatBytes, formatDate, formatEta } from '@/utils/format'
import { jobPhaseLabel } from '@/utils/jobPhase'

const jobs = useJobsStore()
const { isMobile } = useMobile()
const statusFilter = ref<'all' | 'active' | JobStatus>('all')
const typeFilter = ref<JobType | ''>('')
const currentPage = ref(1)
const currentPageSize = ref(20)
const busyJobId = ref<string | null>(null)
const expanded = ref<string[]>([])

const summary = computed(() => ({
  active: jobs.activeJobs.filter((job) => job.status !== 'paused').length,
  paused: jobs.activeJobs.filter((job) => job.status === 'paused').length,
  failed: jobs.items.filter((job) => ['failed', 'canceled'].includes(job.status)).length,
}))

const statusOptions: Array<{ value: typeof statusFilter.value; label: string }> = [
  { value: 'all', label: '全部状态' },
  { value: 'active', label: '全部活动任务' },
  { value: 'queued', label: '排队中' },
  { value: 'preparing', label: '准备中' },
  { value: 'running', label: '进行中' },
  { value: 'post_processing', label: '后处理中' },
  { value: 'paused', label: '已暂停' },
  { value: 'completed', label: '已完成' },
  { value: 'failed', label: '失败' },
  { value: 'canceled', label: '已取消' },
]

const typeOptions = (Object.entries({
  download: '媒体下载', analysis: '综合分析', package: '产物打包', merge: '音视频合并', transcode: '格式转码',
  media_analysis: '媒体分析', asr: '语音转写', ocr: '画面 OCR', scene_detection: '镜头分析', summary: '内容摘要', cleanup: '产物清理',
}) as Array<[JobType, string]>).map(([value, label]) => ({ value, label }))
const companionLabels = { cover: '封面', subtitle: '公开字幕', danmaku: '弹幕 XML', metadata: '元数据' } as const

const statusView: Record<JobStatus, { label: string; type: 'success' | 'warning' | 'danger' | 'info' | 'primary'; icon: typeof Clock }> = {
  queued: { label: '排队中', type: 'info', icon: Clock },
  preparing: { label: '准备中', type: 'primary', icon: Refresh },
  running: { label: '进行中', type: 'primary', icon: VideoPlay },
  post_processing: { label: '后处理中', type: 'primary', icon: Refresh },
  paused: { label: '已暂停', type: 'warning', icon: VideoPause },
  completed: { label: '已完成', type: 'success', icon: CircleCheck },
  canceled: { label: '已取消', type: 'info', icon: CircleClose },
  failed: { label: '失败', type: 'danger', icon: Warning },
}

const typeView: Record<JobType, { label: string; icon: typeof Download }> = {
  download: { label: '媒体下载', icon: Download },
  analysis: { label: '综合分析', icon: MagicStick },
  package: { label: '产物打包', icon: Files },
  merge: { label: '音视频合并', icon: Files },
  transcode: { label: '格式转码', icon: Refresh },
  media_analysis: { label: '媒体分析', icon: MagicStick },
  asr: { label: '语音转写', icon: Document },
  ocr: { label: '画面 OCR', icon: Document },
  scene_detection: { label: '镜头分析', icon: MagicStick },
  summary: { label: '内容摘要', icon: MagicStick },
  cleanup: { label: '产物清理', icon: Files },
}

function progressStatus(job: Job): 'success' | 'exception' | 'warning' | undefined {
  if (job.status === 'completed') return 'success'
  if (job.status === 'failed') return 'exception'
  if (job.status === 'paused') return 'warning'
  return undefined
}

function phaseText(job: Job): string {
  return jobPhaseLabel(job.phase, statusView[job.status].label)
}

function companionNames(job: Job, outcome: 'failed' | 'not_available'): string[] {
  return Object.entries(job.companionOutcomes)
    .filter(([, value]) => value === outcome)
    .map(([type]) => companionLabels[type as keyof typeof companionLabels])
}

async function cancel(job: Job): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定取消“${job.videoTitle || typeView[job.type].label}”吗？已完成的有效产物会保留，半成品不会进入产物列表。`, '取消任务', { type: 'warning', confirmButtonText: '取消任务', cancelButtonText: '继续执行' })
    busyJobId.value = job.id
    await jobs.cancel(job.id)
    ElMessage.success('已提交取消请求')
  } catch (reason) {
    if (reason !== 'cancel' && reason !== 'close') ElMessage.error(reason instanceof Error ? reason.message : '取消任务失败')
  } finally { busyJobId.value = null }
}

async function retry(job: Job): Promise<void> {
  busyJobId.value = job.id
  try { await jobs.retry(job.id); ElMessage.success('任务已重新进入队列') }
  catch (reason) { ElMessage.error(reason instanceof Error ? reason.message : '重试失败') }
  finally { busyJobId.value = null }
}

async function pause(job: Job): Promise<void> {
  busyJobId.value = job.id
  try { await jobs.pause(job.id); ElMessage.success('任务将在安全点暂停') }
  catch (reason) { ElMessage.error(reason instanceof Error ? reason.message : '暂停失败') }
  finally { busyJobId.value = null }
}

async function resume(job: Job): Promise<void> {
  busyJobId.value = job.id
  try { await jobs.resume(job.id); ElMessage.success('任务已继续') }
  catch (reason) { ElMessage.error(reason instanceof Error ? reason.message : '继续任务失败') }
  finally { busyJobId.value = null }
}

function toggleDetails(id: string): void {
  expanded.value = expanded.value.includes(id) ? expanded.value.filter((item) => item !== id) : [...expanded.value, id]
}

async function loadJobs(resetPage = false): Promise<void> {
  if (resetPage) currentPage.value = 1
  await jobs.refresh({
    page: currentPage.value,
    pageSize: currentPageSize.value,
    type: typeFilter.value || undefined,
    status: statusFilter.value !== 'all' && statusFilter.value !== 'active' ? statusFilter.value : undefined,
    activeOnly: statusFilter.value === 'active' || undefined,
  })
  currentPage.value = jobs.page
  currentPageSize.value = jobs.pageSize
}

async function refreshAll(): Promise<void> {
  await Promise.all([loadJobs(), jobs.refreshActive()])
}

watch([statusFilter, typeFilter], () => { void loadJobs(true) })
onMounted(() => void refreshAll())
</script>

<template>
  <div class="jobs-view">
    <PageHeader title="任务中心" description="下载、合并、转码与分析任务统一排队；刷新页面或从后台返回后会恢复当前状态。" eyebrow="TASK CENTER">
      <template #actions>
        <span class="connection-state" :class="{ online: jobs.connected || !jobs.activeCount }"><el-icon><Connection /></el-icon>{{ jobs.connected ? '实时更新已连接' : jobs.activeCount ? '正在恢复实时状态' : '当前无活动任务' }}</span>
        <el-button :icon="Refresh" :loading="jobs.loading || jobs.activeLoading" @click="refreshAll">刷新</el-button>
      </template>
    </PageHeader>

    <section class="summary-grid">
      <article><span class="summary-icon primary"><VideoPlay /></span><div><strong>{{ summary.active }}</strong><small>正在执行</small></div></article>
      <article><span class="summary-icon warning"><VideoPause /></span><div><strong>{{ summary.paused }}</strong><small>已暂停</small></div></article>
      <article><span class="summary-icon success"><CircleCheck /></span><div><strong>{{ jobs.total }}</strong><small>当前筛选总数</small></div></article>
      <article><span class="summary-icon danger"><Warning /></span><div><strong>{{ summary.failed }}</strong><small>本页失败/取消</small></div></article>
    </section>

    <div class="filter-bar surface-card">
      <div class="filter-controls">
        <el-select v-model="statusFilter" aria-label="按任务状态筛选" placeholder="全部状态">
          <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-select v-model="typeFilter" clearable aria-label="按任务类型筛选" placeholder="全部任务类型">
          <el-option v-for="item in typeOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
      </div>
      <span>第 {{ currentPage }} 页 · 共 {{ jobs.total }} 个任务</span>
    </div>

    <RequestError v-if="jobs.error" class="jobs-error" :error="jobs.error" @retry="loadJobs()" />

    <section v-loading="jobs.loading && !jobs.items.length" class="job-list">
      <article v-for="job in jobs.items" :key="job.id" class="job-card surface-card" data-testid="job-card">
        <div class="job-top">
          <span class="type-icon"><el-icon><component :is="typeView[job.type]?.icon || MoreFilled" /></el-icon></span>
          <div class="job-title"><span><el-tag size="small" effect="plain" :type="statusView[job.status].type"><el-icon><component :is="statusView[job.status].icon" /></el-icon>{{ statusView[job.status].label }}</el-tag><small>{{ typeView[job.type]?.label || job.type }}</small></span><h2>{{ job.videoTitle || typeView[job.type]?.label || '后台任务' }}</h2><p>{{ job.partTitle || '任务级操作' }}</p></div>
          <div class="job-times"><small>创建于 {{ formatDate(job.createdAt) }}</small><small v-if="job.finishedAt">完成于 {{ formatDate(job.finishedAt) }}</small></div>
        </div>

        <div class="progress-section">
          <div class="progress-copy"><strong>{{ phaseText(job) }}</strong><span>{{ Math.round(job.progress) }}%</span></div>
          <el-progress :percentage="Math.max(0, Math.min(100, Math.round(job.progress)))" :stroke-width="9" :show-text="false" :status="progressStatus(job)" />
          <div class="progress-meta"><span v-if="job.speedBytesPerSecond"><Download />{{ formatBytes(job.speedBytesPerSecond) }}/s</span><span v-if="job.etaSeconds != null"><Clock />{{ formatEta(job.etaSeconds) }}</span><span>已重试 {{ job.retryCount }} 次</span></div>
        </div>

        <div v-if="job.status === 'failed'" class="job-error" role="alert"><el-icon><Warning /></el-icon><div><strong>{{ job.errorMessage || '任务未能完成' }}</strong><small>错误代码：{{ job.errorCode || 'UNKNOWN' }}。请查看脱敏诊断或重试。</small></div></div>
        <div v-if="companionNames(job, 'failed').length" class="job-warning" role="status"><el-icon><Warning /></el-icon><div><strong>主媒体已完成，部分随附内容失败</strong><small>{{ companionNames(job, 'failed').join('、') }}未能保存；重试会补齐缺失伴随产物，已有有效文件不会丢失。</small></div></div>
        <div v-if="companionNames(job, 'not_available').length" class="job-notice"><el-icon><InfoFilled /></el-icon><div><strong>视频源未提供部分随附内容</strong><small>{{ companionNames(job, 'not_available').join('、') }}不可用，不影响主媒体任务完成。</small></div></div>

        <div v-if="expanded.includes(job.id)" class="job-details">
          <dl><div><dt>任务编号</dt><dd>{{ job.id }}</dd></div><div><dt>当前阶段</dt><dd>{{ job.phase || '暂无' }}</dd></div><div><dt>开始时间</dt><dd>{{ formatDate(job.startedAt) }}</dd></div><div><dt>产物数量</dt><dd>{{ job.artifactIds.length }}</dd></div></dl>
        </div>

        <div class="job-actions">
          <el-button text :icon="MoreFilled" @click="toggleDetails(job.id)">{{ expanded.includes(job.id) ? '收起详情' : '查看详情' }}</el-button>
          <div>
            <el-button v-if="['queued', 'preparing', 'running', 'post_processing'].includes(job.status)" :icon="VideoPause" :loading="busyJobId === job.id" @click="pause(job)">暂停</el-button>
            <el-button v-if="job.status === 'paused'" type="primary" plain :icon="VideoPlay" :loading="busyJobId === job.id" @click="resume(job)">继续</el-button>
            <el-button v-if="['queued', 'preparing', 'running', 'post_processing', 'paused'].includes(job.status)" type="danger" plain :icon="CircleClose" :loading="busyJobId === job.id" @click="cancel(job)">取消</el-button>
            <el-button v-if="job.status === 'failed'" type="primary" :icon="RefreshLeft" :loading="busyJobId === job.id" @click="retry(job)">失败重试</el-button>
            <el-button v-if="job.status === 'completed' && job.artifactIds.length" type="primary" plain :icon="Files" @click="$router.push({ name: 'artifacts', query: { jobId: job.id } })">查看产物</el-button>
          </div>
        </div>
      </article>

      <el-empty v-if="!jobs.loading && !jobs.items.length" :image-size="110" :description="statusFilter === 'all' && !typeFilter ? '还没有任务，从视频详情页创建下载或分析任务' : '当前筛选条件下没有任务'">
        <el-button v-if="statusFilter === 'all' && !typeFilter" type="primary" @click="$router.push('/')">解析一个视频</el-button>
        <el-button v-else @click="statusFilter = 'all'; typeFilter = ''">查看全部任务</el-button>
      </el-empty>

      <el-pagination
        v-if="jobs.total > currentPageSize"
        v-model:current-page="currentPage"
        v-model:page-size="currentPageSize"
        class="pagination"
        :total="jobs.total"
        :page-sizes="[10, 20, 50, 100]"
        :layout="isMobile ? 'prev, pager, next' : 'total, sizes, prev, pager, next'"
        @current-change="loadJobs()"
        @size-change="loadJobs(true)"
      />
    </section>
  </div>
</template>

<style scoped>
.jobs-view { max-width: 1120px; margin: 0 auto; }
.connection-state { display: inline-flex; align-items: center; gap: 6px; min-height: 40px; padding: 0 12px; border: 1px solid var(--line); border-radius: 10px; color: var(--text-tertiary); font-size: 12px; }
.connection-state.online { color: var(--success); }
.summary-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 18px; }
.summary-grid article { display: flex; align-items: center; gap: 13px; padding: 17px; border: 1px solid var(--line-soft); border-radius: 15px; background: var(--surface); }
.summary-icon { display: grid; place-items: center; width: 41px; height: 41px; border-radius: 12px; }
.summary-icon svg { width: 20px; }
.summary-icon.primary { background: var(--brand-soft); color: var(--brand); }.summary-icon.warning { background: #fff3df; color: var(--warning); }.summary-icon.success { background: #e9f8f1; color: var(--success); }.summary-icon.danger { background: #fff0ef; color: var(--danger); }
.summary-grid strong, .summary-grid small { display: block; }.summary-grid strong { font-size: 22px; }.summary-grid small { color: var(--text-tertiary); font-size: 11px; }
.filter-bar { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 16px; padding: 8px; }
.filter-controls { display: flex; gap: 8px; }.filter-controls .el-select { width: 180px; min-height: 44px; }.filter-controls :deep(.el-select__wrapper) { min-height: 44px; }
.filter-bar > span { padding-right: 8px; color: var(--text-tertiary); font-size: 12px; }
.jobs-error { margin-bottom: 15px; }
.job-list { display: grid; gap: 12px; min-height: 240px; }
.job-card { padding: 19px; }
.job-top { display: grid; grid-template-columns: auto 1fr auto; gap: 13px; align-items: start; }
.type-icon { display: grid; place-items: center; width: 43px; height: 43px; border-radius: 12px; background: var(--brand-soft); color: var(--brand); }.type-icon .el-icon { font-size: 21px; }
.job-title { min-width: 0; }.job-title > span { display: flex; align-items: center; gap: 8px; }.job-title > span small { color: var(--text-tertiary); }.job-title h2 { margin: 8px 0 2px; font-size: 16px; overflow-wrap: anywhere; }.job-title p { margin: 0; color: var(--text-secondary); font-size: 12px; overflow-wrap: anywhere; }
.job-times { display: grid; justify-items: end; gap: 4px; color: var(--text-tertiary); font-size: 10px; }
.progress-section { margin: 19px 0 8px; }.progress-copy { display: flex; justify-content: space-between; gap: 15px; margin-bottom: 8px; }.progress-copy strong { font-size: 12px; }.progress-copy span { color: var(--text-secondary); font-size: 12px; }
.progress-meta { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 8px; color: var(--text-tertiary); font-size: 10px; }.progress-meta span { display: flex; align-items: center; gap: 4px; }.progress-meta svg { width: 12px; }
.job-error { display: flex; align-items: flex-start; gap: 9px; margin-top: 13px; padding: 11px; border-radius: 10px; background: #fff1ef; color: var(--danger); }.job-error strong, .job-error small { display: block; }.job-error strong { font-size: 12px; }.job-error small { margin-top: 3px; color: #a85b52; font-size: 10px; }
.job-warning, .job-notice { display: flex; align-items: flex-start; gap: 9px; margin-top: 13px; padding: 11px; border-radius: 10px; }.job-warning { background: #fff7e8; color: #986318; }.job-notice { background: var(--surface-muted); color: var(--text-secondary); }.job-warning strong, .job-warning small, .job-notice strong, .job-notice small { display: block; }.job-warning strong, .job-notice strong { font-size: 12px; }.job-warning small, .job-notice small { margin-top: 3px; font-size: 10px; line-height: 1.5; }
.job-details { margin-top: 14px; padding: 13px; border-radius: 10px; background: var(--surface-muted); }.job-details dl { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 20px; margin: 0; }.job-details dl div { min-width: 0; }.job-details dt { color: var(--text-tertiary); font-size: 10px; }.job-details dd { margin: 4px 0 0; color: var(--text-secondary); font-size: 11px; overflow-wrap: anywhere; }
.job-actions { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--line-soft); }.job-actions > div { display: flex; gap: 8px; }
.pagination { justify-content: flex-end; padding: 16px 0 4px; }

@media (max-width: 767px) {
  .summary-grid { grid-template-columns: 1fr 1fr; gap: 9px; }.summary-grid article { padding: 13px; }.summary-icon { width: 37px; height: 37px; }
  .filter-bar { display: block; padding: 8px; }.filter-controls { display: grid; grid-template-columns: 1fr 1fr; }.filter-controls .el-select { width: 100%; }.filter-bar > span { display: block; padding: 9px 4px 1px; }
  .job-card { padding: 15px; }.job-top { grid-template-columns: auto 1fr; }.job-times { grid-column: 2; justify-items: start; }
  .job-actions { display: grid; grid-template-columns: 1fr; }.job-actions > .el-button { justify-self: start; }.job-actions > div { display: grid; grid-template-columns: 1fr 1fr; }.job-actions > div .el-button { min-height: 44px; margin: 0; }
  .job-details dl { grid-template-columns: 1fr; }
  .pagination { justify-content: center; overflow-x: auto; }
}
@media (max-width: 374px) { .summary-grid, .filter-controls { grid-template-columns: 1fr; }.job-actions > div { grid-template-columns: 1fr; } }
</style>
