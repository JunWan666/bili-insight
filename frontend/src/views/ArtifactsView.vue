<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Calendar,
  ArrowDown,
  ArrowRight,
  Delete,
  Document,
  Download,
  Files,
  Film,
  Headset,
  Link,
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
const selectedIds = ref<string[]>([])
const expandedGroups = ref<string[]>([])
let searchTimer: number | null = null

interface ArtifactGroup {
  key: string
  title: string
  sourceUrl: string | null
  artifacts: Artifact[]
  totalSize: number
  latestCreatedAt: string
  typeLabels: string[]
}

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
const selectedArtifacts = computed(() => artifacts.value.filter((item) => selectedIds.value.includes(item.id)))
const artifactGroups = computed<ArtifactGroup[]>(() => {
  const groups = new Map<string, ArtifactGroup>()
  for (const artifact of artifacts.value) {
    const key = artifact.videoId || 'independent-retained'
    const existing = groups.get(key)
    const typeLabel = typeMap[artifact.type]?.label || artifact.type
    if (existing) {
      existing.artifacts.push(artifact)
      existing.totalSize += artifact.size
      if (artifact.createdAt > existing.latestCreatedAt) existing.latestCreatedAt = artifact.createdAt
      if (!existing.sourceUrl && artifact.sourceUrl) existing.sourceUrl = artifact.sourceUrl
      if (!existing.typeLabels.includes(typeLabel)) existing.typeLabels.push(typeLabel)
      continue
    }
    groups.set(key, {
      key,
      title: artifact.videoId ? (artifact.videoTitle || '未命名视频') : '独立 / 受管保留产物',
      sourceUrl: artifact.sourceUrl,
      artifacts: [artifact],
      totalSize: artifact.size,
      latestCreatedAt: artifact.createdAt,
      typeLabels: [typeLabel],
    })
  }
  return [...groups.values()]
})

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
    selectedIds.value = selectedIds.value.filter((id) => result.items.some((item) => item.id === id))
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

function toggleArtifact(artifact: Artifact, selected: boolean): void {
  selectedIds.value = selected
    ? Array.from(new Set([...selectedIds.value, artifact.id]))
    : selectedIds.value.filter((id) => id !== artifact.id)
}

function isGroupExpanded(group: ArtifactGroup): boolean {
  return expandedGroups.value.includes(group.key)
}

function toggleGroup(group: ArtifactGroup): void {
  expandedGroups.value = isGroupExpanded(group)
    ? expandedGroups.value.filter((key) => key !== group.key)
    : [...expandedGroups.value, group.key]
}

function groupSelected(group: ArtifactGroup): boolean {
  return group.artifacts.every((artifact) => selectedIds.value.includes(artifact.id))
}

function groupIndeterminate(group: ArtifactGroup): boolean {
  const count = group.artifacts.filter((artifact) => selectedIds.value.includes(artifact.id)).length
  return count > 0 && count < group.artifacts.length
}

function toggleGroupSelection(group: ArtifactGroup, selected: boolean): void {
  const ids = group.artifacts.map((artifact) => artifact.id)
  selectedIds.value = selected
    ? Array.from(new Set([...selectedIds.value, ...ids]))
    : selectedIds.value.filter((id) => !ids.includes(id))
}

function downloadSelected(): void {
  for (const artifact of selectedArtifacts.value) downloadArtifact(artifact)
  ElMessage.success(`已提交 ${selectedArtifacts.value.length} 个文件的浏览器下载`)
}

async function removeSelected(): Promise<void> {
  if (!selectedArtifacts.value.length) return
  const totalSize = selectedArtifacts.value.reduce((sum, item) => sum + item.size, 0)
  try {
    await ElMessageBox.confirm(
      `彻底删除选中的 ${selectedArtifacts.value.length} 个产物及文件，预计释放 ${formatBytes(totalSize)}。此操作无法恢复。`,
      '批量删除产物',
      { type: 'warning', confirmButtonText: '全部彻底删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const result = await artifactApi.removeMany(selectedIds.value, true)
    if (result.failedIds.length) ElMessage.warning(`${result.deletedCount} 个已删除，${result.failedIds.length} 个删除失败`)
    else ElMessage.success(`已删除 ${result.deletedCount} 个产物`)
    selectedIds.value = []
    await Promise.all([loadArtifacts(), loadDisk()])
  } catch (reason) {
    ElMessage.error(toApiError(reason).message)
  }
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

    <section v-if="selectedIds.length" class="batch-bar surface-card">
      <strong>已选择 {{ selectedIds.length }} 个产物</strong>
      <span>共 {{ formatBytes(selectedArtifacts.reduce((sum, item) => sum + item.size, 0)) }}</span>
      <el-button :icon="Download" @click="downloadSelected">批量保存</el-button>
      <el-button type="danger" plain :icon="Delete" @click="removeSelected">批量删除</el-button>
      <el-button text @click="selectedIds = []">取消选择</el-button>
    </section>

    <section v-loading="loading" class="artifact-content surface-card">
      <div v-if="artifactGroups.length" class="artifact-groups">
        <section v-for="group in artifactGroups" :key="group.key" class="artifact-group" data-testid="artifact-group">
          <header class="artifact-group-header">
            <el-checkbox :model-value="groupSelected(group)" :indeterminate="groupIndeterminate(group)" :aria-label="`选择 ${group.title} 的全部产物`" @change="toggleGroupSelection(group, Boolean($event))" />
            <button type="button" class="artifact-group-toggle" data-testid="artifact-group-toggle" :aria-expanded="isGroupExpanded(group)" @click="toggleGroup(group)">
              <span class="group-icon"><Files /></span>
              <span class="group-copy"><strong>{{ group.title }}</strong><small>{{ group.artifacts.length }} 个产物 · {{ formatBytes(group.totalSize) }} · {{ group.typeLabels.join('、') }}</small></span>
              <span class="group-time">最近 {{ formatDate(group.latestCreatedAt) }}</span>
              <el-icon><ArrowDown v-if="isGroupExpanded(group)" /><ArrowRight v-else /></el-icon>
            </button>
            <a v-if="group.sourceUrl" class="group-source" :href="group.sourceUrl" target="_blank" rel="noopener noreferrer"><el-icon><Link /></el-icon>官方源视频</a>
          </header>

          <div v-if="isGroupExpanded(group)" class="artifact-group-content">
            <div class="desktop-artifacts">
              <el-table :data="group.artifacts" row-key="id">
                <el-table-column width="48"><template #default="{ row }"><el-checkbox :model-value="selectedIds.includes(row.id)" :aria-label="`选择 ${row.filename}`" @change="toggleArtifact(row, Boolean($event))" /></template></el-table-column>
                <el-table-column label="产物" min-width="320"><template #default="{ row }"><div class="artifact-name"><span><el-icon><component :is="typeMap[row.type]?.icon || Files" /></el-icon></span><div><strong>{{ row.filename }}</strong><small>{{ row.retained ? (row.protected ? '用户受管保留' : '历史已清理 · 文件受管保留') : (row.partTitle || '整集产物') }}</small><small v-if="analysisContext(row)" class="analysis-context">{{ analysisContext(row) }}</small></div></div></template></el-table-column>
                <el-table-column label="类型" width="110"><template #default="{ row }"><el-tag effect="plain" type="info">{{ typeMap[row.type]?.label || row.type }}</el-tag></template></el-table-column>
                <el-table-column label="任务状态" width="125"><template #default="{ row }"><el-tag v-if="row.retained" effect="plain" type="warning">受管保留</el-tag><el-tag v-else-if="row.jobStatus" effect="plain" :type="jobStatusMap[row.jobStatus].type">{{ jobStatusMap[row.jobStatus].label }}</el-tag><span v-else>暂无</span></template></el-table-column>
                <el-table-column label="文件信息" min-width="160"><template #default="{ row }">{{ formatBytes(row.size) }}<small class="cell-sub">{{ row.mediaInfo?.container || row.mimeType }}<template v-if="row.mediaInfo?.duration"> · {{ formatDuration(row.mediaInfo.duration) }}</template></small></template></el-table-column>
                <el-table-column label="创建时间" min-width="150"><template #default="{ row }">{{ formatDate(row.createdAt) }}<small class="cell-sub">{{ row.expiresAt ? `清理于 ${formatDate(row.expiresAt)}` : '手动保留' }}</small></template></el-table-column>
                <el-table-column label="操作" width="236" fixed="right"><template #default="{ row }"><el-button text :icon="View" @click="openDetails(row)">详情</el-button><el-button text type="primary" :icon="Download" @click="downloadArtifact(row)">保存</el-button><el-button text type="danger" :icon="Delete" aria-label="删除产物" @click="removeArtifact(row)" /></template></el-table-column>
              </el-table>
            </div>

            <div class="mobile-artifacts">
              <article v-for="artifact in group.artifacts" :key="artifact.id" data-testid="artifact-card">
                <el-checkbox class="artifact-checkbox" :model-value="selectedIds.includes(artifact.id)" :aria-label="`选择 ${artifact.filename}`" @change="toggleArtifact(artifact, Boolean($event))" />
                <div class="artifact-name"><span><el-icon><component :is="typeMap[artifact.type]?.icon || Files" /></el-icon></span><div><strong>{{ artifact.filename }}</strong><small>{{ artifact.retained ? (artifact.protected ? '用户受管保留' : '历史已清理 · 文件受管保留') : (artifact.partTitle || '整集产物') }}</small><small v-if="analysisContext(artifact)" class="analysis-context">{{ analysisContext(artifact) }}</small></div></div>
                <div class="artifact-meta"><span><el-tag size="small" effect="plain" type="info">{{ typeMap[artifact.type]?.label }}</el-tag></span><span v-if="artifact.retained"><el-tag size="small" effect="plain" type="warning">受管保留</el-tag></span><span v-else-if="artifact.jobStatus"><el-tag size="small" effect="plain" :type="jobStatusMap[artifact.jobStatus].type">{{ jobStatusMap[artifact.jobStatus].label }}</el-tag></span><span>{{ formatBytes(artifact.size) }}</span><span>{{ formatDate(artifact.createdAt) }}</span></div>
                <div class="artifact-actions"><el-button :icon="View" @click="openDetails(artifact)">详情</el-button><el-button type="primary" plain :icon="Download" @click="downloadArtifact(artifact)">保存到设备</el-button><el-button type="danger" plain :icon="Delete" aria-label="删除产物" @click="removeArtifact(artifact)" /></div>
              </article>
            </div>
          </div>
        </section>
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
          <div class="detail-actions"><el-button v-if="selected.sourceUrl" tag="a" :href="selected.sourceUrl" target="_blank" rel="noopener noreferrer" :icon="Link">官方源视频</el-button><el-button type="primary" :icon="Download" @click="downloadArtifact(selected)">保存到设备</el-button><el-button type="danger" plain :icon="Delete" @click="removeArtifact(selected)">删除</el-button></div>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.artifacts-view { width: 100%; }
.disk-card { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 14px; margin-bottom: 11px; padding: 12px 14px; }
.disk-icon { display: grid; place-items: center; width: 43px; height: 43px; border-radius: 12px; background: var(--brand-soft); color: var(--brand); }.disk-icon svg { width: 21px; }
.disk-copy { display: grid; gap: 8px; min-width: 0; }.disk-copy > div { display: flex; justify-content: space-between; gap: 15px; }.disk-copy span, .disk-copy small { color: var(--text-tertiary); font-size: 11px; }.disk-card a { font-weight: 650; text-decoration: none; }
.filters { display: grid; grid-template-columns: minmax(220px, 1fr) 150px 155px minmax(270px, auto) auto; gap: 8px; margin-bottom: 11px; padding: 8px; }
.artifact-error { margin-bottom: 15px; }
.batch-bar { display: flex; align-items: center; gap: 9px; margin-bottom: 11px; padding: 9px 12px; }.batch-bar > span { margin-right: auto; color: var(--text-tertiary); font-size: 11px; }
.artifact-content { min-height: 320px; overflow: hidden; }.cell-sub { display: block; margin-top: 4px; color: var(--text-tertiary); font-size: 10px; }
.artifact-groups { min-height: 0; }.artifact-group + .artifact-group { border-top: 1px solid var(--line-soft); }.artifact-group-header { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 10px; min-height: 68px; padding: 8px 14px; }.artifact-group-toggle { display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto; align-items: center; gap: 11px; min-width: 0; padding: 0; border: 0; background: transparent; color: inherit; text-align: left; cursor: pointer; }.group-icon { display: grid; place-items: center; width: 38px; height: 38px; border-radius: 8px; background: var(--brand-soft); color: var(--brand); }.group-icon svg { width: 19px; }.group-copy { min-width: 0; }.group-copy strong, .group-copy small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.group-copy strong { font-size: 13px; }.group-copy small, .group-time { margin-top: 4px; color: var(--text-tertiary); font-size: 10px; }.group-time { margin: 0; white-space: nowrap; }.group-source { display: inline-flex; align-items: center; gap: 5px; color: var(--brand); font-size: 11px; font-weight: 650; text-decoration: none; white-space: nowrap; }.artifact-group-content { border-top: 1px solid var(--line-soft); background: var(--surface-muted); }
.artifact-group-toggle { min-height: 44px; }
.artifact-name { display: flex; align-items: center; gap: 11px; min-width: 0; }.artifact-name > span { display: grid; place-items: center; flex: 0 0 auto; width: 38px; height: 38px; border-radius: 10px; background: var(--brand-soft); color: var(--brand); }.artifact-name strong, .artifact-name small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.artifact-name strong { max-width: 360px; }.artifact-name small { max-width: 360px; margin-top: 3px; color: var(--text-tertiary); font-size: 10px; }
.desktop-artifacts :deep(.el-button + .el-button) { margin-left: 4px; }.mobile-artifacts { display: none; }.pagination { justify-content: flex-end; padding: 16px; border-top: 1px solid var(--line-soft); }
.artifact-detail { min-height: 300px; }.artifact-detail h2 { margin: 20px 0; font-size: 19px; overflow-wrap: anywhere; }.media-preview, .image-preview { display: block; width: 100%; max-height: 360px; border-radius: 13px; background: #0d0f14; object-fit: contain; }.audio-preview { width: 100%; }.text-preview { max-height: 380px; margin: 0; padding: 14px; overflow: auto; border-radius: 12px; background: var(--surface-muted); color: var(--text-secondary); font: 12px/1.65 ui-monospace, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }.file-preview { display: grid; place-items: center; gap: 10px; min-height: 180px; padding: 20px; border-radius: 13px; background: var(--surface-muted); color: var(--text-tertiary); text-align: center; }.file-preview .el-icon { font-size: 40px; }.artifact-detail dl { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }.artifact-detail dt { color: var(--text-tertiary); font-size: 10px; }.artifact-detail dd { margin: 5px 0 0; color: var(--text-secondary); overflow-wrap: anywhere; }.checksum { font-family: ui-monospace, monospace; font-size: 11px; }.detail-actions { display: flex; gap: 9px; margin-top: 24px; }
@media (min-width: 1200px) {
  .artifact-content { display: flex; flex-direction: column; height: calc(100dvh - 292px); min-height: 320px; }
  .artifact-groups { overflow: auto; }.desktop-artifacts { min-height: 0; overflow: auto; }
  .artifact-name strong, .artifact-name small { max-width: none; }
  .pagination { flex: 0 0 auto; }
}
@media (min-width: 1200px) and (max-width: 1279px) { .artifact-content { height: calc(100dvh - 345px); } }
@media (max-width: 1279px) { .filters { grid-template-columns: 1fr 150px; }.filters :deep(.el-date-editor) { width: 100%; } }
@media (max-width: 767px) {
  .batch-bar { position: sticky; top: 72px; z-index: 8; display: grid; grid-template-columns: 1fr 1fr; }.batch-bar strong, .batch-bar > span { grid-column: 1 / -1; }.batch-bar .el-button { margin: 0; min-height: 44px; }
  .disk-card { grid-template-columns: auto 1fr; }.disk-card a { grid-column: 2; }.disk-copy > div { display: grid; gap: 4px; }
  .filters { grid-template-columns: 1fr; }.filters :deep(.el-date-editor) { width: 100%; }
  .artifact-group-header { grid-template-columns: auto minmax(0, 1fr); padding: 10px 12px; }.artifact-group-toggle { grid-template-columns: auto minmax(0, 1fr) auto; }.group-time { display: none; }.group-source { grid-column: 2; min-height: 32px; }.desktop-artifacts { display: none; }.mobile-artifacts { display: grid; gap: 0; }.mobile-artifacts article { padding: 16px; border-bottom: 1px solid var(--line-soft); background: var(--surface); }.artifact-name strong, .artifact-name small { max-width: calc(100vw - 115px); }.artifact-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 13px; margin: 13px 0; color: var(--text-tertiary); font-size: 11px; }.artifact-actions { display: grid; grid-template-columns: auto 1fr auto; gap: 7px; }.artifact-actions .el-button { min-height: 44px; margin: 0; }
  .pagination { justify-content: center; overflow-x: auto; }.artifact-detail dl { grid-template-columns: 1fr; }
}
</style>
