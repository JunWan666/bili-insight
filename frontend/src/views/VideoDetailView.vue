<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ArrowLeft,
  Calendar,
  Clock,
  CollectionTag,
  DataAnalysis,
  Document,
  Files,
  Lock,
  MagicStick,
  Star,
  User,
  VideoCamera,
  View,
} from '@element-plus/icons-vue'
import AnalysisConfigDrawer from '@/components/analysis/AnalysisConfigDrawer.vue'
import AnalysisResultsPanel from '@/components/analysis/AnalysisResultsPanel.vue'
import StreamComparisonChart from '@/components/charts/StreamComparisonChart.vue'
import BatchDownloadDrawer from '@/components/download/BatchDownloadDrawer.vue'
import DownloadConfigDrawer from '@/components/download/DownloadConfigDrawer.vue'
import RequestError from '@/components/RequestError.vue'
import VideoPreviewDialog from '@/components/preview/VideoPreviewDialog.vue'
import StreamSelector from '@/components/streams/StreamSelector.vue'
import { settingsApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { useJobsStore } from '@/stores/jobs'
import { useVideosStore } from '@/stores/videos'
import type { DownloadPreset, Job, MediaStream, VideoPart } from '@/types/api'
import { formatBitrate, formatDate, formatDuration, formatNumber } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const videos = useVideosStore()
const jobs = useJobsStore()

const selectedPartId = ref('')
const selectedVideoId = ref<string | null>(null)
const selectedAudioId = ref<string | null>(null)
const preset = ref<DownloadPreset>('best_quality')
const defaultFilenameTemplate = ref('{title} - P{page}')
const defaultContainer = ref<'mp4' | 'mkv'>('mp4')
const minimumResolutionHeight = ref<360 | 480 | 720 | 1080 | null>(null)
const activeTab = ref('streams')
const downloadOpen = ref(false)
const batchDownloadOpen = ref(false)
const analysisOpen = ref(false)
const previewOpen = ref(false)
const refreshingIdentity = ref(false)
const verifyingStreams = ref(false)
const descriptionExpanded = ref(false)

const video = computed(() => videos.current)
const streams = computed(() => videos.streams)
const selectedPart = computed<VideoPart | null>(() => video.value?.parts.find((part) => part.id === selectedPartId.value) ?? video.value?.parts[0] ?? null)
const selectedVideoStream = computed<MediaStream | null>(() => streams.value?.videos.find((stream) => stream.id === selectedVideoId.value) ?? null)
const selectedAudioStream = computed<MediaStream | null>(() => streams.value?.audios.find((stream) => stream.id === selectedAudioId.value) ?? null)
const actualAccessMode = computed<'anonymous' | 'authenticated'>(() => streams.value?.accessModeUsed ?? video.value?.accessModeUsed ?? 'anonymous')

const accessCopy = computed(() => {
  if (actualAccessMode.value === 'authenticated') {
    return { type: 'authenticated', title: '本次已使用登录态', text: auth.isPremium ? '已按大会员权益解析，所有规格仍以视频源实际返回为准。' : '已按当前登录权益解析实际可访问规格。' }
  }
  if (auth.isAuthenticated || video.value?.authAvailable) {
    return { type: 'available', title: '登录态可用，但本次未使用', text: '当前结果完全来自匿名请求；可由你主动补充登录画质。' }
  }
  return { type: 'anonymous', title: '本次使用匿名模式', text: '当前没有可用登录态，已展示匿名可实际访问的媒体流。' }
})

const relatedJobs = computed<Job[]>(() => {
  const unique = new Map([...jobs.activeJobs, ...jobs.items].map((job) => [job.id, job]))
  return [...unique.values()].filter((job) => job.videoId === video.value?.id)
})
const analysisJobs = computed(() => relatedJobs.value.filter((job) => ['analysis', 'media_analysis', 'asr', 'ocr', 'scene_detection', 'summary'].includes(job.type)))

const streamMetrics = computed(() => {
  const list = streams.value?.videos ?? []
  const codecs = [...new Set(list.map((item) => item.codec))]
  const maxBitrate = Math.max(0, ...list.map((item) => item.bitrate ?? 0))
  const verified = list.filter((item) => item.verifiedAt).length
  return { codecs, maxBitrate, verified }
})

async function loadDetail(): Promise<void> {
  const id = String(route.params.videoId)
  try {
    const loaded = await videos.load(id)
    const requested = typeof route.query.part === 'string' ? route.query.part : ''
    selectedPartId.value = loaded.parts.some((part) => part.id === requested)
      ? requested
      : loaded.selectedPartId && loaded.parts.some((part) => part.id === loaded.selectedPartId)
        ? loaded.selectedPartId
        : loaded.parts[0]?.id ?? ''
    if (selectedPartId.value && streams.value?.partId !== selectedPartId.value) {
      await videos.loadStreams(id, selectedPartId.value, loaded.accessModeUsed)
    }
  } catch {
    // Safe store error is rendered below.
  }
}

async function changePart(partId: string): Promise<void> {
  selectedPartId.value = partId
  selectedVideoId.value = null
  selectedAudioId.value = null
  await router.replace({ query: { ...route.query, part: partId } })
  if (!video.value) return
  try {
    await videos.loadStreams(video.value.id, partId, actualAccessMode.value)
  } catch {
    // Safe store error is rendered in stream area.
  }
}

async function changeAccess(mode: 'anonymous' | 'authenticated'): Promise<void> {
  if (!video.value || !selectedPart.value) return
  if (mode === 'authenticated' && !auth.isAuthenticated) {
    await router.push({ name: 'settings', query: { section: 'auth', returnTo: route.fullPath } })
    return
  }
  refreshingIdentity.value = true
  try {
    await videos.refresh(mode)
    await videos.loadStreams(video.value.id, selectedPart.value.id, mode)
    ElMessage.success(mode === 'authenticated' ? '已补充登录态可用规格' : '已切换为匿名规格')
  } catch {
    // Store renders actionable error.
  } finally {
    refreshingIdentity.value = false
  }
}

function openDownload(): void {
  if (!selectedPart.value || (!selectedVideoStream.value && !selectedAudioStream.value)) {
    ElMessage.warning('请先选择需要下载的媒体流')
    return
  }
  downloadOpen.value = true
}

function openPreview(): void {
  if (!selectedPart.value || !selectedVideoStream.value) {
    ElMessage.warning('请先选择需要预览的视频流')
    return
  }
  if (!selectedVideoStream.value.previewSupported) {
    ElMessage.warning('该媒体流暂不具备浏览器预览信息，请重新解析或直接下载')
    return
  }
  previewOpen.value = true
}

function handleJobCreated(): void {
  void jobs.refresh({ page: 1, pageSize: 20 })
}

async function verifySelectedStreams(streamIds: string[]): Promise<void> {
  if (!streamIds.length) return
  verifyingStreams.value = true
  try {
    for (const streamId of streamIds) {
      await videos.verifyStream(streamId, actualAccessMode.value)
    }
    ElMessage.success('所选媒体流可读取')
  } catch {
    // Store renders the actionable verification error.
  } finally {
    verifyingStreams.value = false
  }
}

async function loadDownloadDefaults(): Promise<void> {
  try {
    const settings = await settingsApi.get()
    preset.value = settings.download.defaultPreset
    defaultFilenameTemplate.value = settings.download.filenameTemplate
    defaultContainer.value = settings.download.defaultContainer
    minimumResolutionHeight.value = settings.download.minimumResolutionHeight
  } catch {
    preset.value = 'best_quality'
  }
}

async function consumeAuthenticatedReturn(): Promise<void> {
  if (route.query.useAuth !== '1') return
  const cleanQuery = { ...route.query }
  delete cleanQuery.useAuth
  try {
    if (!auth.status) await auth.load().catch(() => undefined)
    if (!auth.isAuthenticated) {
      ElMessage.warning('登录态未生效，请重新上传或继续匿名使用')
      return
    }
    await changeAccess('authenticated')
  } finally {
    await router.replace({ query: cleanQuery })
  }
}

async function initialize(): Promise<void> {
  await Promise.all([
    loadDetail(),
    loadDownloadDefaults(),
    auth.status ? Promise.resolve() : auth.load().catch(() => undefined),
  ])
  void jobs.refresh({ page: 1, pageSize: 20 })
  await consumeAuthenticatedReturn()
}

watch(() => route.params.videoId, loadDetail)
onMounted(() => void initialize())
</script>

<template>
  <div class="video-detail">
    <button class="back-link" type="button" @click="$router.push('/')"><el-icon><ArrowLeft /></el-icon>返回解析页</button>

    <div v-if="videos.loading && !video" class="loading-layout">
      <el-skeleton animated><template #template><div class="skeleton-head"><el-skeleton-item variant="image" /><div><el-skeleton-item variant="h1" /><el-skeleton-item variant="text" /><el-skeleton-item variant="text" /></div></div><el-skeleton-item class="skeleton-panel" variant="rect" /></template></el-skeleton>
    </div>

    <RequestError v-else-if="videos.error && !video" :error="videos.error" @retry="loadDetail" />

    <template v-else-if="video">
      <section class="video-head surface-card">
        <div class="cover-wrap">
          <img :src="video.coverUrl" :alt="`${video.title} 封面`" referrerpolicy="no-referrer" />
          <span><el-icon><Clock /></el-icon>{{ formatDuration(video.duration) }}</span>
        </div>
        <div class="video-copy">
          <div class="identity-line"><el-tag effect="plain">{{ video.bvid }}</el-tag><el-tag v-if="video.rights.copyright" type="info" effect="plain">{{ video.rights.copyright }}</el-tag><el-tag v-if="video.rights.isPaid" type="danger" effect="plain">付费内容</el-tag><el-tag v-if="video.rights.isPremiumOnly" type="warning" effect="plain">会员内容</el-tag><span>{{ video.fromCache ? '缓存数据' : '实时解析' }} · {{ formatDate(video.parsedAt) }}</span></div>
          <h1>{{ video.title }}</h1>
          <div class="meta-row"><span><el-icon><User /></el-icon>{{ video.ownerName }}</span><span><el-icon><Calendar /></el-icon>{{ formatDate(video.publishedAt) }}</span><span><el-icon><CollectionTag /></el-icon>{{ video.parts.length }} 个分 P</span></div>
          <div class="stat-row"><span><el-icon><View /></el-icon>{{ formatNumber(video.statistics.views) }} 播放</span><span><el-icon><Star /></el-icon>{{ formatNumber(video.statistics.favorites) }} 收藏</span><span><el-icon><Document /></el-icon>{{ formatNumber(video.statistics.danmaku) }} 弹幕</span></div>
          <div v-if="video.tags.length" class="tags"><el-tag v-for="tag in video.tags" :key="tag" size="small" type="info" effect="plain">{{ tag }}</el-tag></div>
          <p v-if="video.description" class="description" :class="{ expanded: descriptionExpanded }">{{ video.description }}</p>
          <button v-if="video.description.length > 120" class="description-toggle" type="button" @click="descriptionExpanded = !descriptionExpanded">{{ descriptionExpanded ? '收起简介' : '展开简介' }}</button>
        </div>
      </section>

      <div class="detail-controls">
        <section class="access-banner" :class="`is-${accessCopy.type}`">
          <span class="access-icon"><Lock /></span>
          <div><strong>{{ accessCopy.title }}</strong><p>{{ accessCopy.text }}</p></div>
          <el-button v-if="actualAccessMode === 'anonymous'" :loading="refreshingIdentity" data-testid="supplement-auth-quality" @click="changeAccess('authenticated')">{{ auth.isAuthenticated ? '补充登录画质' : '前往上传 Cookie' }}</el-button>
          <el-button v-else :loading="refreshingIdentity" @click="changeAccess('anonymous')">切换匿名规格</el-button>
        </section>

        <section v-if="video.parts.length" class="part-bar surface-card">
          <div><span>{{ video.provider === 'bilibili_pgc' ? '当前剧集' : '当前分 P' }}</span><strong>{{ selectedPart?.pageNumber }} / {{ video.parts.length }}</strong></div>
          <div class="part-controls">
            <el-select :modelValue="selectedPartId" filterable :placeholder="video.provider === 'bilibili_pgc' ? '选择剧集' : '选择分 P'" @update:modelValue="changePart">
              <el-option v-for="part in video.parts" :key="part.id" :value="part.id" :label="`${video.provider === 'bilibili_pgc' ? 'EP' : 'P'}${part.pageNumber} · ${part.title}`"><span class="part-option">{{ video.provider === 'bilibili_pgc' ? 'EP' : 'P' }}{{ part.pageNumber }} · {{ part.title }}<small>{{ formatDuration(part.duration) }}</small></span></el-option>
            </el-select>
            <el-button v-if="video.parts.length > 1" :icon="Files" data-testid="open-batch-download" @click="batchDownloadOpen = true">批量下载</el-button>
          </div>
          <span class="part-duration"><el-icon><Clock /></el-icon>{{ formatDuration(selectedPart?.duration) }}</span>
        </section>
      </div>

      <RequestError v-if="videos.error" class="detail-error" :error="videos.error" />

      <section class="workspace surface-card">
        <div class="workspace-tabs" role="tablist" aria-label="视频详情分区">
          <button type="button" role="tab" :aria-selected="activeTab === 'streams'" :class="{ active: activeTab === 'streams' }" data-testid="tab-streams" @click="activeTab = 'streams'"><el-icon><VideoCamera /></el-icon>媒体流</button>
          <button type="button" role="tab" :aria-selected="activeTab === 'technical'" :class="{ active: activeTab === 'technical' }" data-testid="tab-technical-analysis" @click="activeTab = 'technical'"><el-icon><DataAnalysis /></el-icon>技术分析</button>
          <button type="button" role="tab" :aria-selected="activeTab === 'content'" :class="{ active: activeTab === 'content' }" data-testid="tab-content-analysis" @click="activeTab = 'content'"><el-icon><MagicStick /></el-icon>内容分析</button>
        </div>

        <div class="workspace-body">
          <div v-if="videos.streamsLoading" class="streams-loading"><el-skeleton :rows="6" animated /></div>
          <StreamSelector
            v-else-if="activeTab === 'streams' && streams"
            v-model:preset="preset"
            v-model:selectedVideoId="selectedVideoId"
            v-model:selectedAudioId="selectedAudioId"
            :streams="streams"
            :minimum-resolution-height="minimumResolutionHeight"
            :verifying="verifyingStreams"
            @configure="openDownload"
            @preview="openPreview"
            @verify="verifySelectedStreams"
          />

          <div v-else-if="activeTab === 'technical'" class="technical-tab" role="tabpanel" aria-label="技术分析">
            <div class="tab-heading"><div><h2>当前媒体能力对比</h2><p>图表来自实际解析到的媒体流；正式分析任务会使用 FFprobe 等工具输出产物报告。</p></div><el-button type="primary" plain @click="analysisOpen = true">创建技术分析</el-button></div>
            <div class="metric-grid">
              <article><small>视频流</small><strong>{{ streams?.videos.length ?? 0 }}</strong><span>个实际规格</span></article>
              <article><small>编码</small><strong>{{ streamMetrics.codecs.length }}</strong><span>{{ streamMetrics.codecs.join(' / ') || '暂无' }}</span></article>
              <article><small>最高码率</small><strong>{{ formatBitrate(streamMetrics.maxBitrate) }}</strong><span>以接口返回值为准</span></article>
              <article><small>已读取验证</small><strong>{{ streamMetrics.verified }} / {{ streams?.videos.length ?? 0 }}</strong><span>其余将在任务前验证</span></article>
            </div>
            <StreamComparisonChart :streams="streams?.videos ?? []" />
            <AnalysisResultsPanel
              v-if="selectedPart"
              :video-id="video.id"
              :part-id="selectedPart.id"
              kind="technical"
              @create="analysisOpen = true"
            />
          </div>

          <div v-else-if="activeTab === 'content'" class="content-tab" role="tabpanel" aria-label="内容分析">
            <div class="tab-heading"><div><h2>按需运行分析能力</h2><p>基础概览、媒体、字幕、ASR、OCR、镜头与摘要可独立选择，已完成的结果不会因其他步骤失败而丢失。</p></div><el-button type="primary" :icon="MagicStick" @click="analysisOpen = true">配置分析</el-button></div>
            <div class="analysis-capabilities">
              <article><span>L0</span><h3>基础内容</h3><p>元数据、标签、统计与公开字幕检查，无需下载媒体。</p></article>
              <article><span>L1</span><h3>媒体技术</h3><p>编码、色彩、响度、相对频谱、镜头与关键帧；语音/音乐区段仅作启发式参考。</p></article>
              <article><span>L2</span><h3>文本提取</h3><p>优先公开字幕；可选 ASR 与 OCR，保留时间戳和置信度。</p></article>
              <article><span>L3</span><h3>语义摘要</h3><p>结合文本与关键帧，输出可定位证据的自动分析结果。</p></article>
            </div>
            <AnalysisResultsPanel
              v-if="selectedPart"
              :video-id="video.id"
              :part-id="selectedPart.id"
              kind="content"
              @create="analysisOpen = true"
            />
            <div v-if="analysisJobs.length" class="related-jobs">
              <div class="related-heading"><h3>相关分析任务</h3><RouterLink to="/jobs">查看全部</RouterLink></div>
              <RouterLink v-for="job in analysisJobs.slice(0, 4)" :key="job.id" class="related-job" to="/jobs"><span><strong>{{ job.phase || job.type }}</strong><small>{{ formatDate(job.createdAt) }}</small></span><el-progress :percentage="Math.round(job.progress)" :status="job.status === 'failed' ? 'exception' : job.status === 'completed' ? 'success' : undefined" /></RouterLink>
            </div>
          </div>
        </div>
      </section>

      <DownloadConfigDrawer
        v-if="selectedPart"
        v-model="downloadOpen"
        :video="video"
        :part="selectedPart"
        :videoStream="selectedVideoStream"
        :audioStream="selectedAudioStream"
        :preset="preset"
        :accessMode="actualAccessMode"
        :default-filename-template="defaultFilenameTemplate"
        :default-container="defaultContainer"
        @created="handleJobCreated"
      />
      <BatchDownloadDrawer
        v-if="video.parts.length > 1"
        v-model="batchDownloadOpen"
        :video="video"
        :initial-part-id="selectedPart?.id"
        :preset="preset"
        :access-mode="actualAccessMode"
        :default-filename-template="defaultFilenameTemplate"
        :default-container="defaultContainer"
        :minimum-resolution-height="minimumResolutionHeight"
        @created="handleJobCreated"
      />
      <AnalysisConfigDrawer v-if="selectedPart" v-model="analysisOpen" :video="video" :part="selectedPart" :accessMode="actualAccessMode" @created="handleJobCreated" />
      <VideoPreviewDialog
        v-if="selectedPart"
        v-model="previewOpen"
        :video="video"
        :part="selectedPart"
        :video-stream="selectedVideoStream"
        :audio-stream="selectedAudioStream"
        :access-mode="actualAccessMode"
      />
    </template>
  </div>
</template>

<style scoped>
.video-detail { display: flex; flex-direction: column; width: 100%; min-width: 0; min-height: 0; }
.back-link { display: inline-flex; align-items: center; gap: 7px; min-height: 44px; margin: -8px 0 17px; padding: 0; border: 0; background: transparent; color: var(--text-secondary); cursor: pointer; }
.back-link:hover { color: var(--brand); }
.video-head { display: grid; grid-template-columns: minmax(260px, 360px) 1fr; gap: 22px; padding: 18px; }
.cover-wrap { position: relative; align-self: start; aspect-ratio: 16 / 10; overflow: hidden; border-radius: 14px; background: var(--surface-muted); }
.cover-wrap img { width: 100%; height: 100%; object-fit: cover; }
.cover-wrap > span { position: absolute; right: 10px; bottom: 10px; display: flex; align-items: center; gap: 5px; padding: 5px 8px; border-radius: 7px; background: rgba(18, 20, 25, .8); color: white; font-size: 12px; }
.video-copy { min-width: 0; }
.identity-line { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; color: var(--text-tertiary); font-size: 11px; }
.video-copy h1 { margin: 10px 0 11px; font-size: 27px; line-height: 1.28; letter-spacing: 0; overflow-wrap: anywhere; }
.meta-row, .stat-row { display: flex; flex-wrap: wrap; gap: 14px 20px; color: var(--text-secondary); font-size: 12px; }
.meta-row span, .stat-row span { display: flex; align-items: center; gap: 5px; }
.stat-row { margin-top: 8px; }
.tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.description { display: -webkit-box; margin: 9px 0 0; overflow: hidden; color: var(--text-secondary); line-height: 1.55; white-space: pre-wrap; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.description.expanded { display: block; }
.description-toggle { min-height: 44px; padding: 0; border: 0; background: transparent; color: var(--brand); cursor: pointer; }
.detail-controls { display: grid; grid-template-columns: minmax(360px, .82fr) minmax(520px, 1.18fr); gap: 12px; margin: 12px 0; }
.access-banner { display: flex; align-items: center; gap: 12px; min-width: 0; margin: 0; padding: 11px 13px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); }
.access-icon { display: grid; place-items: center; flex: 0 0 auto; width: 38px; height: 38px; border-radius: 11px; background: var(--surface-muted); color: var(--text-secondary); }
.access-banner > div { flex: 1; }
.access-banner strong { font-size: 13px; }
.access-banner p { margin: 4px 0 0; color: var(--text-secondary); font-size: 12px; line-height: 1.45; }
.access-banner.is-authenticated { border-color: #a9ddca; background: #effaf5; }
.access-banner.is-authenticated .access-icon { background: #d9f4e8; color: #147654; }
.access-banner.is-available { border-color: #c8cff5; background: var(--brand-soft); }
.access-banner.is-available .access-icon { background: #dfe4ff; color: var(--brand); }
.detail-error { margin-bottom: 16px; }
.part-bar { display: flex; align-items: center; gap: 13px; min-width: 0; margin: 0; padding: 10px 13px; }
.part-bar > div { display: grid; min-width: 80px; }
.part-bar > div span { color: var(--text-tertiary); font-size: 10px; }
.part-bar > div strong { margin-top: 3px; font-size: 13px; }
.part-controls { display: flex; flex: 1; align-items: center; gap: 8px; min-width: 0; }.part-controls .el-select { flex: 1; max-width: none; }
.part-duration { display: flex; align-items: center; gap: 5px; margin-left: auto; color: var(--text-secondary); }
.part-option { display: flex; justify-content: space-between; gap: 20px; }
.part-option small { color: var(--text-tertiary); }
.workspace { display: flex; flex: 1 1 auto; flex-direction: column; min-height: 430px; overflow: hidden; }
.workspace-tabs { display: flex; gap: 3px; padding: 10px 14px 0; border-bottom: 1px solid var(--line-soft); }
.workspace-tabs button { display: flex; align-items: center; gap: 7px; min-height: 48px; padding: 0 17px; border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--text-secondary); font-weight: 650; cursor: pointer; }
.workspace-tabs button.active { border-bottom-color: var(--brand); color: var(--brand); }
.workspace-body { flex: 1 1 auto; min-height: 0; padding: 18px; overflow: auto; overscroll-behavior: contain; }
.streams-loading { padding: 20px 0; }
.tab-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 22px; }
.tab-heading h2 { margin: 0; font-size: 20px; }
.tab-heading p { max-width: 750px; margin: 6px 0 0; color: var(--text-secondary); line-height: 1.6; }
.metric-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 22px; }
.metric-grid article { padding: 16px; border: 1px solid var(--line-soft); border-radius: 13px; background: var(--surface-muted); }
.metric-grid small, .metric-grid strong, .metric-grid span { display: block; }
.metric-grid small { color: var(--text-tertiary); }
.metric-grid strong { margin-top: 8px; font-size: 19px; overflow-wrap: anywhere; }
.metric-grid span { margin-top: 4px; color: var(--text-secondary); font-size: 11px; overflow-wrap: anywhere; }
.analysis-capabilities { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.analysis-capabilities article { padding: 17px; border: 1px solid var(--line-soft); border-radius: 13px; }
.analysis-capabilities article > span { display: inline-block; padding: 3px 7px; border-radius: 6px; background: var(--brand-soft); color: var(--brand); font-size: 10px; font-weight: 750; }
.analysis-capabilities h3 { margin: 11px 0 6px; font-size: 14px; }
.analysis-capabilities p { margin: 0; color: var(--text-secondary); font-size: 11px; line-height: 1.55; }
.related-jobs { margin-top: 24px; padding-top: 20px; border-top: 1px solid var(--line-soft); }
.related-heading { display: flex; align-items: center; justify-content: space-between; }
.related-heading h3 { margin: 0; font-size: 14px; }
.related-heading a { font-size: 12px; text-decoration: none; }
.related-job { display: grid; grid-template-columns: minmax(130px, .7fr) 1fr; align-items: center; gap: 20px; margin-top: 10px; padding: 12px; border-radius: 10px; background: var(--surface-muted); color: inherit; text-decoration: none; }
.related-job strong, .related-job small { display: block; }
.related-job small { margin-top: 3px; color: var(--text-tertiary); font-size: 10px; }
.skeleton-head { display: grid; grid-template-columns: 40% 1fr; gap: 28px; }
.skeleton-head > .el-skeleton__item { height: 260px; }
.skeleton-head > div { display: grid; align-content: center; gap: 15px; }
.skeleton-panel { height: 380px; margin-top: 20px; }

@media (max-width: 980px) {
  .video-head { grid-template-columns: 330px 1fr; }
  .detail-controls { grid-template-columns: 1fr; }
  .metric-grid, .analysis-capabilities { grid-template-columns: repeat(2, 1fr); }
}
@media (min-width: 1200px) {
  .video-detail { height: 100%; }
  .back-link { min-height: 32px; margin: -4px 0 8px; }
  .workspace { min-height: 0; }
}
@media (max-width: 767px) {
  .video-head { grid-template-columns: 1fr; gap: 18px; padding: 14px; }
  .cover-wrap { margin: -14px -14px 0; border-radius: 17px 17px 0 0; }
  .video-copy h1 { font-size: 22px; }
  .detail-controls { display: block; margin: 14px 0; }
  .access-banner { margin-bottom: 12px; }
  .access-banner { align-items: flex-start; flex-wrap: wrap; padding: 14px; }
  .access-banner .el-button { width: 100%; margin-left: 52px; min-height: 44px; }
  .part-bar { display: grid; grid-template-columns: auto 1fr; gap: 10px; }
  .part-controls { grid-column: 1 / -1; display: grid; grid-template-columns: minmax(0, 1fr) auto; grid-row: 2; }.part-controls .el-select { width: 100%; max-width: none; }
  .part-duration { grid-column: 2; grid-row: 1; margin: 0; justify-self: end; }
  .workspace-tabs { display: grid; grid-template-columns: repeat(3, 1fr); padding-inline: 6px; }
  .workspace-tabs button { justify-content: center; min-width: 0; padding-inline: 4px; font-size: 12px; }
  .workspace-body { padding: 16px; }
  .tab-heading { display: block; }
  .tab-heading .el-button { width: 100%; min-height: 46px; margin-top: 14px; }
  .metric-grid, .analysis-capabilities { grid-template-columns: 1fr 1fr; }
  .related-job { grid-template-columns: 1fr; gap: 8px; }
  .skeleton-head { grid-template-columns: 1fr; }
  .skeleton-head > .el-skeleton__item { height: 210px; }
}
@media (max-width: 374px) {
  .workspace-tabs button .el-icon { display: none; }
  .metric-grid, .analysis-capabilities { grid-template-columns: 1fr; }
  .access-banner .el-button { margin-left: 0; }
  .part-controls { grid-template-columns: 1fr; }.part-controls .el-button { min-height: 44px; margin: 0; }
}
</style>
