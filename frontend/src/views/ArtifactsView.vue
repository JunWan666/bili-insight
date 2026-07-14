<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Calendar,
  Delete,
  Document,
  Download,
  Files,
  Film,
  Headset,
  Picture,
  Refresh,
  Search,
  View,
} from '@element-plus/icons-vue'
import { artifactApi } from '@/api'
import { toApiError, type ApiError } from '@/api/errors'
import PageHeader from '@/components/PageHeader.vue'
import RequestError from '@/components/RequestError.vue'
import type { Artifact, ArtifactType, JobStatus, StorageStatus } from '@/types/api'
import { endOfLocalDayIso } from '@/utils/dateRange'
import { formatBytes, formatDate, formatDuration } from '@/utils/format'

const route = useRoute()
const artifacts = ref<Artifact[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const query = ref('')
const type = ref<ArtifactType | ''>('')
const jobStatus = ref<JobStatus | ''>('')
const dateRange = ref<[Date, Date] | null>(null)
const loading = ref(false)
const error = ref<ApiError | null>(null)
const selected = ref<Artifact | null>(null)
const detailOpen = ref(false)
const detailLoading = ref(false)
const textPreview = ref('')
const disk = ref<StorageStatus | null>(null)
let searchTimer: number | null = null

const types: Array<{ value: ArtifactType; label: string; icon: typeof Film }> = [
  { value: 'video', label: '视频', icon: Film },
  { value: 'audio', label: '音频', icon: Headset },
  { value: 'cover', label: '封面', icon: Picture },
  { value: 'subtitle', label: '字幕', icon: Document },
  { value: 'danmaku', label: '弹幕 XML', icon: Document },
  { value: 'transcript', label: '转写', icon: Document },
  { value: 'keyframe', label: '关键帧', icon: Picture },
  { value: 'report', label: '分析报告', icon: Document },
  { value: 'metadata', label: '元数据', icon: Document },
  { value: 'archive', label: '归档包', icon: Files },
]

const typeMap = Object.fromEntries(types.map((item) => [item.value, item])) as Record<ArtifactType, typeof types[number]>
const analysisFeatureLabels: Record<string, string> = {
  basic: '基础概览',
  media: '媒体技术',
  audio: '音频分析',
  subtitles: '公开字幕',
  asr: 'ASR 转写',
  ocr: 'OCR 文字',
  scenes: '镜头分析',
  summary: '内容摘要',
}
const artifactRoleLabels: Record<string, string> = {
  analysis_report: '分析报告',
  analysis_manifest: '分析清单',
  keyframe: '关键帧',
  subtitle_export: '字幕导出',
  transcript_export: '转写导出',
  edited_text_export: '人工编辑文本导出',
  edited_text_report: '人工编辑报告',
}
const jobStatusMap: Record<JobStatus, { label: string; type: 'primary' | 'success' | 'warning' | 'danger' | 'info' }> = {
  queued: { label: '排队中', type: 'info' },
  preparing: { label: '准备中', type: 'primary' },
  running: { label: '处理中', type: 'primary' },
  post_processing: { label: '后处理中', type: 'primary' },
  paused: { label: '已暂停', type: 'warning' },
  completed: { label: '已完成', type: 'success' },
  canceled: { label: '已取消', type: 'info' },
  failed: { label: '部分/全部失败', type: 'danger' },
}
const usedBytes = computed(() => disk.value ? Math.max(0, disk.value.totalBytes - disk.value.freeBytes) : 0)
const usagePercent = computed(() => disk.value?.totalBytes ? Math.round((usedBytes.value / disk.value.totalBytes) * 100) : 0)

function analysisContext(artifact: Artifact): string | null {
  const media = artifact.mediaInfo
  if (!media) return null
  if (!media.analysisFeature && !media.artifactRole && !media.format && media.editRevision === null) return null
  return [
    media.analysisFeature ? (analysisFeatureLabels[media.analysisFeature] ?? media.analysisFeature) : null,
    media.artifactRole ? (artifactRoleLabels[media.artifactRole] ?? media.artifactRole) : null,
    media.format?.toUpperCase(),
    media.timestampSeconds === null ? null : `时间 ${formatDuration(media.timestampSeconds)}`,
    media.editRevision === null ? null : `人工修订 #${media.editRevision}`,
    media.source === 'edited' ? 'edited 来源' : null,
  ].filter(Boolean).join(' · ')
}

async function loadArtifacts(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const result = await artifactApi.list({
      query: query.value.trim() || undefined,
      type: type.value || undefined,
      status: jobStatus.value || undefined,
      jobId: typeof route.query.jobId === 'string' ? route.query.jobId : undefined,
      from: dateRange.value?.[0].toISOString(),
      to: dateRange.value ? endOfLocalDayIso(dateRange.value[1]) : undefined,
      page: page.value,
      pageSize: pageSize.value,
    })
    artifacts.value = result.items
    total.value = result.total
  } catch (reason) {
    error.value = toApiError(reason)
  } finally { loading.value = false }
}

async function loadDisk(): Promise<void> {
  try { disk.value = await artifactApi.storage() } catch { disk.value = null }
}

function scheduleSearch(): void {
  if (searchTimer !== null) window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => { page.value = 1; void loadArtifacts() }, 350)
}

async function openDetails(artifact: Artifact): Promise<void> {
  detailOpen.value = true
  detailLoading.value = true
  textPreview.value = ''
  try {
    selected.value = await artifactApi.get(artifact.id)
    if ((selected.value.mimeType.startsWith('text/') || selected.value.mimeType.includes('json')) && selected.value.size <= 5 * 1024 * 1024) {
      textPreview.value = await (await artifactApi.content(artifact.id)).text()
    }
  } catch (reason) {
    ElMessage.error(toApiError(reason).message)
  } finally { detailLoading.value = false }
}

function downloadArtifact(artifact: Artifact): void {
  const anchor = document.createElement('a')
  anchor.href = artifactApi.contentUrl(artifact.id)
  anchor.download = artifact.filename
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}

async function removeArtifact(artifact: Artifact): Promise<void> {
  let deleteFile = artifact.retained
  try {
    if (artifact.retained) {
      await ElMessageBox.confirm(
        `“${artifact.filename}”是受管保留文件。删除后将释放 ${formatBytes(artifact.size)} 磁盘空间且无法恢复。`,
        '彻底删除保留文件',
        { type: 'warning', confirmButtonText: '记录与文件一起删除', cancelButtonText: '取消' },
      )
    } else {
      await ElMessageBox.confirm(
        `删除“${artifact.filename}”。选择“记录与文件”将释放 ${formatBytes(artifact.size)} 磁盘空间；选择“仅删除记录”会把文件转为可继续管理的受管保留文件。`,
        '删除产物',
        { type: 'warning', confirmButtonText: '记录与文件', cancelButtonText: '仅删除记录', distinguishCancelAndClose: true },
      )
      deleteFile = true
    }
  } catch (action) {
    if (!artifact.retained && action === 'cancel') deleteFile = false
    else return
  }
  try {
    const result = await artifactApi.remove(artifact.id, deleteFile)
    if (deleteFile && !result.fileDeleted) {
      ElMessage.warning('记录已删除，但未能确认文件已释放；系统将继续清理残留文件')
    } else if (!deleteFile && !result.retained) {
      ElMessage.warning('记录已删除，但原文件不存在，未创建受管保留文件')
    } else {
      ElMessage.success(deleteFile ? '记录与文件已删除' : '文件已转为受管保留，可继续下载或彻底删除')
    }
    if (selected.value?.id === artifact.id) detailOpen.value = false
    await Promise.all([loadArtifacts(), loadDisk()])
  } catch (reason) { ElMessage.error(toApiError(reason).message) }
}

function changeType(): void { page.value = 1; void loadArtifacts() }
function changeDate(): void { page.value = 1; void loadArtifacts() }

watch(() => route.query.jobId, () => { page.value = 1; void loadArtifacts() })
onMounted(() => { void loadArtifacts(); void loadDisk() })
</script>

<template>
  <div class="artifacts-view">
    <PageHeader title="产物与历史" description="统一管理下载文件、字幕、转写、关键帧与分析报告。文件通过支持断点读取的产物接口交付。" eyebrow="ARTIFACT LIBRARY">
      <template #actions><el-button :icon="Refresh" :loading="loading" @click="loadArtifacts">刷新</el-button></template>
    </PageHeader>

    <section v-if="disk" class="disk-card surface-card">
      <span class="disk-icon"><Files /></span>
      <div class="disk-copy"><div><strong>本机存储</strong><span>已用 {{ formatBytes(usedBytes) }} / {{ formatBytes(disk.totalBytes) }}</span></div><el-progress :percentage="usagePercent" :stroke-width="8" :show-text="false" :status="usagePercent >= 90 ? 'exception' : usagePercent >= 75 ? 'warning' : undefined" /><small>受管产物 {{ formatBytes(disk.artifactBytes) }} · 可用 {{ formatBytes(disk.freeBytes) }}</small></div>
      <RouterLink to="/settings?section=storage">存储设置</RouterLink>
    </section>

    <section class="filters surface-card">
      <el-input v-model="query" clearable placeholder="搜索标题或文件名" :prefix-icon="Search" @input="scheduleSearch" />
      <el-select v-model="type" clearable placeholder="全部类型" @change="changeType"><el-option v-for="item in types" :key="item.value" :label="item.label" :value="item.value" /></el-select>
      <el-select v-model="jobStatus" clearable placeholder="全部状态" @change="changeType"><el-option label="已完成" value="completed" /><el-option label="部分/全部失败" value="failed" /><el-option label="已取消" value="canceled" /></el-select>
      <el-date-picker v-model="dateRange" type="daterange" range-separator="至" start-placeholder="开始日期" end-placeholder="结束日期" :prefix-icon="Calendar" @change="changeDate" />
      <el-button v-if="route.query.jobId" @click="$router.replace({ name: 'artifacts' })">清除任务筛选</el-button>
    </section>

    <RequestError v-if="error" class="artifact-error" :error="error" @retry="loadArtifacts" />

    <section v-loading="loading" class="artifact-content surface-card">
      <div v-if="artifacts.length" class="desktop-artifacts">
        <el-table :data="artifacts" row-key="id">
          <el-table-column label="产物" min-width="320">
            <template #default="{ row }"><div class="artifact-name"><span><el-icon><component :is="typeMap[row.type]?.icon || Files" /></el-icon></span><div><strong>{{ row.filename }}</strong><small>{{ row.retained ? (row.protected ? '用户受管保留' : '历史已清理 · 文件受管保留') : (row.videoTitle || '独立产物') }}</small><small v-if="analysisContext(row)" class="analysis-context">{{ analysisContext(row) }}</small></div></div></template>
          </el-table-column>
          <el-table-column label="类型" width="110"><template #default="{ row }"><el-tag effect="plain" type="info">{{ typeMap[row.type]?.label || row.type }}</el-tag></template></el-table-column>
          <el-table-column label="任务状态" width="125"><template #default="{ row }"><el-tag v-if="row.retained" effect="plain" type="warning">受管保留</el-tag><el-tag v-else-if="row.jobStatus" effect="plain" :type="jobStatusMap[row.jobStatus].type">{{ jobStatusMap[row.jobStatus].label }}</el-tag><span v-else>暂无</span></template></el-table-column>
          <el-table-column label="文件信息" min-width="160"><template #default="{ row }">{{ formatBytes(row.size) }}<small class="cell-sub">{{ row.mediaInfo?.container || row.mimeType }}<template v-if="row.mediaInfo?.duration"> · {{ formatDuration(row.mediaInfo.duration) }}</template></small></template></el-table-column>
          <el-table-column label="创建时间" min-width="150"><template #default="{ row }">{{ formatDate(row.createdAt) }}<small class="cell-sub">{{ row.expiresAt ? `清理于 ${formatDate(row.expiresAt)}` : '手动保留' }}</small></template></el-table-column>
          <el-table-column label="操作" width="210" fixed="right"><template #default="{ row }"><el-button text :icon="View" @click="openDetails(row)">详情</el-button><el-button text type="primary" :icon="Download" @click="downloadArtifact(row)">保存</el-button><el-button text type="danger" :icon="Delete" @click="removeArtifact(row)" /></template></el-table-column>
        </el-table>
      </div>

      <div v-if="artifacts.length" class="mobile-artifacts">
        <article v-for="artifact in artifacts" :key="artifact.id" data-testid="artifact-card">
          <div class="artifact-name"><span><el-icon><component :is="typeMap[artifact.type]?.icon || Files" /></el-icon></span><div><strong>{{ artifact.filename }}</strong><small>{{ artifact.retained ? (artifact.protected ? '用户受管保留' : '历史已清理 · 文件受管保留') : (artifact.videoTitle || '独立产物') }}</small><small v-if="analysisContext(artifact)" class="analysis-context">{{ analysisContext(artifact) }}</small></div></div>
          <div class="artifact-meta"><span><el-tag size="small" effect="plain" type="info">{{ typeMap[artifact.type]?.label }}</el-tag></span><span v-if="artifact.retained"><el-tag size="small" effect="plain" type="warning">受管保留</el-tag></span><span v-else-if="artifact.jobStatus"><el-tag size="small" effect="plain" :type="jobStatusMap[artifact.jobStatus].type">{{ jobStatusMap[artifact.jobStatus].label }}</el-tag></span><span>{{ formatBytes(artifact.size) }}</span><span>{{ formatDate(artifact.createdAt) }}</span></div>
          <div class="artifact-actions"><el-button :icon="View" @click="openDetails(artifact)">详情</el-button><el-button type="primary" plain :icon="Download" @click="downloadArtifact(artifact)">保存到设备</el-button><el-button type="danger" plain :icon="Delete" aria-label="删除产物" @click="removeArtifact(artifact)" /></div>
        </article>
      </div>

      <el-empty v-if="!loading && !artifacts.length" :image-size="110" description="没有符合条件的产物"><el-button type="primary" @click="$router.push('/')">解析并下载视频</el-button></el-empty>

      <el-pagination v-if="total > pageSize" v-model:current-page="page" v-model:page-size="pageSize" class="pagination" :total="total" :page-sizes="[20, 50, 100]" layout="total, sizes, prev, pager, next" @change="loadArtifacts" />
    </section>

    <el-drawer v-model="detailOpen" direction="rtl" size="min(620px, 100%)" title="产物详情">
      <div v-loading="detailLoading" class="artifact-detail">
        <template v-if="selected">
          <video v-if="selected.mimeType.startsWith('video/')" class="media-preview" controls preload="metadata" :src="artifactApi.contentUrl(selected.id)" />
          <audio v-else-if="selected.mimeType.startsWith('audio/')" class="audio-preview" controls preload="metadata" :src="artifactApi.contentUrl(selected.id)" />
          <img v-else-if="selected.mimeType.startsWith('image/')" class="image-preview" :src="artifactApi.contentUrl(selected.id)" :alt="selected.filename" />
          <pre v-else-if="textPreview" class="text-preview">{{ textPreview }}</pre>
          <div v-else class="file-preview"><el-icon><Document /></el-icon><span>该格式不提供浏览器内预览，可保存到设备后查看。</span></div>

          <h2>{{ selected.filename }}</h2>
          <dl><div><dt>类型</dt><dd>{{ typeMap[selected.type]?.label || selected.type }}</dd></div><div v-if="selected.retained"><dt>保留状态</dt><dd><el-tag size="small" effect="plain" type="warning">{{ selected.protected ? '用户受管保留' : '历史已清理 · 等待存储策略' }}</el-tag></dd></div><div v-else-if="selected.jobStatus"><dt>任务状态</dt><dd><el-tag size="small" effect="plain" :type="jobStatusMap[selected.jobStatus].type">{{ jobStatusMap[selected.jobStatus].label }}</el-tag></dd></div><div v-if="selected.partTitle"><dt>分 P</dt><dd>{{ selected.partTitle }}</dd></div><div v-if="analysisContext(selected)"><dt>分析产物</dt><dd>{{ analysisContext(selected) }}</dd></div><div><dt>大小</dt><dd>{{ formatBytes(selected.size) }}</dd></div><div><dt>MIME</dt><dd>{{ selected.mimeType }}</dd></div><div><dt>创建时间</dt><dd>{{ formatDate(selected.createdAt) }}</dd></div><div v-if="selected.retainedAt"><dt>转为保留</dt><dd>{{ formatDate(selected.retainedAt) }}</dd></div><div><dt>校验值</dt><dd class="checksum">{{ selected.checksum || '暂无' }}</dd></div><div><dt>清理时间</dt><dd>{{ selected.protected ? '仅手动彻底删除' : (selected.expiresAt ? formatDate(selected.expiresAt) : '按存储清理周期') }}</dd></div></dl>
          <div class="detail-actions"><el-button type="primary" :icon="Download" @click="downloadArtifact(selected)">保存到设备</el-button><el-button type="danger" plain :icon="Delete" @click="removeArtifact(selected)">删除</el-button></div>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.artifacts-view { max-width: 1200px; margin: 0 auto; }
.disk-card { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 16px; margin-bottom: 15px; padding: 17px; }
.disk-icon { display: grid; place-items: center; width: 43px; height: 43px; border-radius: 12px; background: var(--brand-soft); color: var(--brand); }.disk-icon svg { width: 21px; }
.disk-copy { display: grid; gap: 8px; min-width: 0; }.disk-copy > div { display: flex; justify-content: space-between; gap: 15px; }.disk-copy span, .disk-copy small { color: var(--text-tertiary); font-size: 11px; }.disk-card a { font-weight: 650; text-decoration: none; }
.filters { display: grid; grid-template-columns: minmax(200px, 1fr) 145px 150px minmax(250px, auto) auto; gap: 10px; margin-bottom: 15px; padding: 12px; }
.artifact-error { margin-bottom: 15px; }
.artifact-content { min-height: 320px; overflow: hidden; }.cell-sub { display: block; margin-top: 4px; color: var(--text-tertiary); font-size: 10px; }
.artifact-name { display: flex; align-items: center; gap: 11px; min-width: 0; }.artifact-name > span { display: grid; place-items: center; flex: 0 0 auto; width: 38px; height: 38px; border-radius: 10px; background: var(--brand-soft); color: var(--brand); }.artifact-name strong, .artifact-name small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.artifact-name strong { max-width: 360px; }.artifact-name small { max-width: 360px; margin-top: 3px; color: var(--text-tertiary); font-size: 10px; }
.mobile-artifacts { display: none; }.pagination { justify-content: flex-end; padding: 16px; border-top: 1px solid var(--line-soft); }
.artifact-detail { min-height: 300px; }.artifact-detail h2 { margin: 20px 0; font-size: 19px; overflow-wrap: anywhere; }.media-preview, .image-preview { display: block; width: 100%; max-height: 360px; border-radius: 13px; background: #0d0f14; object-fit: contain; }.audio-preview { width: 100%; }.text-preview { max-height: 380px; margin: 0; padding: 14px; overflow: auto; border-radius: 12px; background: var(--surface-muted); color: var(--text-secondary); font: 12px/1.65 ui-monospace, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }.file-preview { display: grid; place-items: center; gap: 10px; min-height: 180px; padding: 20px; border-radius: 13px; background: var(--surface-muted); color: var(--text-tertiary); text-align: center; }.file-preview .el-icon { font-size: 40px; }.artifact-detail dl { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }.artifact-detail dt { color: var(--text-tertiary); font-size: 10px; }.artifact-detail dd { margin: 5px 0 0; color: var(--text-secondary); overflow-wrap: anywhere; }.checksum { font-family: ui-monospace, monospace; font-size: 11px; }.detail-actions { display: flex; gap: 9px; margin-top: 24px; }
@media (max-width: 1000px) { .filters { grid-template-columns: 1fr 150px; }.filters :deep(.el-date-editor) { width: 100%; } }
@media (max-width: 767px) {
  .disk-card { grid-template-columns: auto 1fr; }.disk-card a { grid-column: 2; }.disk-copy > div { display: grid; gap: 4px; }
  .filters { grid-template-columns: 1fr; }.filters :deep(.el-date-editor) { width: 100%; }
  .desktop-artifacts { display: none; }.mobile-artifacts { display: grid; gap: 0; }.mobile-artifacts article { padding: 16px; border-bottom: 1px solid var(--line-soft); }.artifact-name strong, .artifact-name small { max-width: calc(100vw - 115px); }.artifact-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 13px; margin: 13px 0; color: var(--text-tertiary); font-size: 11px; }.artifact-actions { display: grid; grid-template-columns: auto 1fr auto; gap: 7px; }.artifact-actions .el-button { min-height: 44px; margin: 0; }
  .pagination { justify-content: center; overflow-x: auto; }.artifact-detail dl { grid-template-columns: 1fr; }
}
</style>
