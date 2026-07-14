<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Clock, Download, EditPen, FolderOpened, WarningFilled } from '@element-plus/icons-vue'
import { analysisApi, artifactApi } from '@/api'
import { toApiError } from '@/api/errors'
import AnalysisTimelineChart from '@/components/charts/AnalysisTimelineChart.vue'
import SpectrumOverviewChart from '@/components/charts/SpectrumOverviewChart.vue'
import TranscriptEditDrawer from './TranscriptEditDrawer.vue'
import type {
  AnalysisCanonicalFeature,
  AnalysisRecord,
  EditTranscriptRequest,
} from '@/types/api'
import { formatBitrate, formatBytes, formatDate, formatDuration } from '@/utils/format'

const props = defineProps<{ record: AnalysisRecord }>()
const emit = defineEmits<{ edited: [] }>()

interface AnalysisTimelineSeries {
  name: string
  points: Array<{ timestampSeconds: number; value: number }>
}

interface AnalysisTimelineInterval {
  startSeconds: number
  endSeconds: number
  label: string
}

const transcriptPage = ref(1)
const transcriptPageSize = 20
const editOpen = ref(false)
const editSaving = ref(false)
const editErrorMessage = ref<string | null>(null)

const featureMeta: Record<AnalysisCanonicalFeature, { title: string; level: string }> = {
  basic: { title: '基础内容概览', level: 'L0' },
  media: { title: '媒体技术报告', level: 'L1' },
  audio: { title: '音频响度、频谱与区段', level: 'L1' },
  subtitles: { title: '公开字幕', level: 'L2' },
  asr: { title: '语音转写 ASR', level: 'L2' },
  ocr: { title: '画面文字 OCR', level: 'L2' },
  scenes: { title: '镜头与关键帧', level: 'L2' },
  summary: { title: '内容摘要与证据', level: 'L3' },
}

const statusMeta = computed(() => {
  if (props.record.status === 'completed') return { label: '已完成', type: 'success' as const }
  if (props.record.status === 'running') return { label: '分析中', type: 'primary' as const }
  if (props.record.status === 'canceled') return { label: '已取消', type: 'info' as const }
  return { label: '单步失败', type: 'danger' as const }
})

const transcript = computed(() => props.record.result.transcript)
const transcriptSegments = computed(() => transcript.value?.segments ?? [])
const visibleTranscriptSegments = computed(() => {
  const start = (transcriptPage.value - 1) * transcriptPageSize
  return transcriptSegments.value.slice(start, start + transcriptPageSize)
})

const audioSeries = computed<AnalysisTimelineSeries[]>(() => {
  const curve = props.record.result.audio?.loudnessCurve ?? []
  const definitions: Array<{ name: string; value: (point: typeof curve[number]) => number | null }> = [
    { name: '瞬时响度', value: (point) => point.momentaryLufs },
    { name: '短时响度', value: (point) => point.shortTermLufs },
    { name: '综合响度', value: (point) => point.integratedLufs },
  ]
  return definitions.map((definition) => ({
    name: definition.name,
    points: curve.flatMap((point) => {
      const value = definition.value(point)
      return value === null ? [] : [{ timestampSeconds: point.timestampSeconds, value }]
    }),
  })).filter((item) => item.points.length > 0)
})

const audioIntervals = computed<AnalysisTimelineInterval[]>(() => (
  props.record.result.audio?.silenceIntervals.map((interval, index) => ({
    startSeconds: interval.startSeconds,
    endSeconds: interval.endSeconds,
    label: `静音 ${index + 1}`,
  })) ?? []
))

const audioSummary = computed(() => {
  const report = props.record.result.audio
  if (!report) return '暂无可解释的音频分析数据。'
  const loudness = report.integratedLoudnessLufs === null
    ? '综合响度暂无'
    : `综合响度 ${report.integratedLoudnessLufs.toFixed(1)} LUFS`
  return `${loudness}；共识别 ${report.silenceIntervals.length} 段静音，响度曲线包含 ${report.loudnessCurve.length} 个采样点。`
})

const spectrumSummary = computed(() => {
  const spectrum = props.record.result.audio?.spectrumOverview
  if (!spectrum) return '暂无频谱概览。'
  const dominant = spectrum.dominantFrequencyHz === null ? '主导频率暂无' : `主导频率约 ${Math.round(spectrum.dominantFrequencyHz)} Hz`
  const centroid = spectrum.spectralCentroidHz === null ? '频谱质心暂无' : `频谱质心约 ${Math.round(spectrum.spectralCentroidHz)} Hz`
  return `${dominant}，${centroid}；柱高为本音频内部归一化的相对频带强度。`
})

const contentSeries = computed<AnalysisTimelineSeries[]>(() => {
  const segments = props.record.result.audio?.contentClassification?.segments ?? []
  const labels: Array<{ value: typeof segments[number]['label']; name: string }> = [
    { value: 'speech_likely', name: '可能语音' },
    { value: 'music_likely', name: '可能音乐' },
    { value: 'mixed_or_uncertain', name: '混合/不确定' },
    { value: 'silence', name: '静音' },
  ]
  return labels.map((label) => ({
    name: label.name,
    points: segments.filter((segment) => segment.label === label.value).map((segment) => ({
      timestampSeconds: (segment.startSeconds + segment.endSeconds) / 2,
      value: segment.confidence * 100,
    })),
  })).filter((series) => series.points.length)
})

const contentSummary = computed(() => {
  const classification = props.record.result.audio?.contentClassification
  if (!classification) return '暂无启发式音频区段。'
  const counts = classification.segments.reduce<Record<string, number>>((result, segment) => {
    result[segment.label] = (result[segment.label] ?? 0) + 1
    return result
  }, {})
  return `粗粒度区段 ${classification.segments.length} 段：可能语音 ${counts.speech_likely ?? 0}，可能音乐 ${counts.music_likely ?? 0}，混合/不确定 ${counts.mixed_or_uncertain ?? 0}，静音 ${counts.silence ?? 0}。`
})

const sceneSeries = computed<AnalysisTimelineSeries[]>(() => [{
  name: '镜头长度',
  points: (props.record.result.scenes?.scenes ?? []).map((scene) => ({
    timestampSeconds: scene.startSeconds,
    value: scene.durationSeconds,
  })),
}])

const sceneSummary = computed(() => {
  const report = props.record.result.scenes
  if (!report) return '暂无可解释的镜头分析数据。'
  const average = report.averageSceneLengthSeconds === null ? '暂无' : `${report.averageSceneLengthSeconds.toFixed(2)} 秒`
  const density = report.sceneDensityPerMinute === null ? '暂无' : `${report.sceneDensityPerMinute.toFixed(2)} 个/分钟`
  return `共 ${report.scenes.length} 个镜头，平均镜头长度 ${average}，场景密度 ${density}，提取 ${report.keyframes.length} 张关键帧。`
})

const warnings = computed(() => {
  const result = props.record.result
  return result.media?.warnings
    ?? result.audio?.warnings
    ?? result.transcript?.warnings
    ?? result.scenes?.warnings
    ?? result.summary?.warnings
    ?? []
})

const displayModel = computed(() => {
  const result = props.record.result
  const name = props.record.modelName
    ?? result.audio?.analyzerName
    ?? result.transcript?.modelName
    ?? result.scenes?.analyzerName
    ?? result.summary?.modelName
    ?? result.media?.probeName
  const version = props.record.modelVersion
    ?? result.audio?.analyzerVersion
    ?? result.transcript?.modelVersion
    ?? result.scenes?.analyzerVersion
    ?? result.summary?.modelVersion
    ?? result.media?.probeVersion
  return { name, version }
})

const exportIds = computed(() => props.record.result.artifactIds.slice(0, 6))

const completedResultAvailable = computed(() => {
  const result = props.record.result
  return Boolean(result.basic || result.media || result.audio || result.transcript || result.scenes || result.summary)
})

function sourceLabel(source: string): string {
  const labels: Record<string, string> = {
    public_subtitle: '公开字幕',
    asr: 'ASR',
    ocr: 'OCR',
    edited: '人工编辑',
    metadata: '元数据',
    scene: '镜头',
    keyframe: '关键帧',
  }
  return labels[source] ?? source
}

function evidenceLocation(evidence: { startSeconds: number | null; endSeconds: number | null; locator: string | null }): string {
  if (evidence.startSeconds !== null) {
    const end = evidence.endSeconds === null ? evidence.startSeconds : evidence.endSeconds
    return `${formatDuration(evidence.startSeconds)} – ${formatDuration(end)}`
  }
  return evidence.locator || '元数据字段'
}

function capabilityLabel(status: string): string {
  return { available: '可用', limited: '能力有限', unavailable: '不可用' }[status] ?? status
}

function coverageLabel(value: string): string {
  return {
    metadata_only: '仅元数据有限概览',
    text_evidence: '元数据 + 时间轴文本',
    text_and_visual_evidence: '元数据 + 时间轴文本 + 结构化画面证据',
  }[value] ?? value
}

function openEditor(): void {
  editErrorMessage.value = null
  editOpen.value = true
}

async function saveEdit(request: EditTranscriptRequest): Promise<void> {
  editSaving.value = true
  editErrorMessage.value = null
  try {
    await analysisApi.editTranscript(props.record.id, request)
    editOpen.value = false
    ElMessage.success('已创建人工编辑版本，原始文本与原始产物保持不变')
    emit('edited')
  } catch (reason) {
    const error = toApiError(reason)
    editErrorMessage.value = `${error.message}；${error.action}`
  } finally {
    editSaving.value = false
  }
}

function confidenceLabel(value: number | null): string {
  return value === null ? '未提供' : `${Math.round(value * 100)}%`
}

function subtitleAvailabilityLabel(value: string | null): string {
  const labels: Record<string, string> = {
    available: '存在公开字幕',
    found: '存在公开字幕',
    not_found: '未发现公开字幕',
    unavailable: '未发现公开字幕',
    not_checked: '尚未检查',
  }
  return value ? (labels[value] ?? value) : '暂无'
}

function audioContentLabel(value: string): string {
  const labels: Record<string, string> = {
    silence: '静音',
    speech_likely: '可能语音',
    music_likely: '可能音乐',
    mixed_or_uncertain: '混合 / 不确定',
  }
  return labels[value] ?? '不确定'
}

function decimal(value: number | null, unit: string, digits = 1): string {
  return value === null ? '暂无' : `${value.toFixed(digits)} ${unit}`
}

function resolution(width: number | null, height: number | null): string {
  return width === null || height === null ? '暂无' : `${width} × ${height}`
}

watch(() => props.record.id, () => {
  transcriptPage.value = 1
  editOpen.value = false
  editErrorMessage.value = null
})
</script>

<template>
  <article class="analysis-record" :data-testid="`analysis-result-${record.feature}`">
    <header class="record-header">
      <div>
        <span class="level">{{ featureMeta[record.feature].level }}</span>
        <div><h3>{{ featureMeta[record.feature].title }}</h3><small>更新于 {{ formatDate(record.updatedAt) }}</small></div>
      </div>
      <el-tag :type="statusMeta.type" effect="plain">{{ statusMeta.label }}</el-tag>
    </header>

    <div v-if="record.status === 'failed'" class="step-state is-failed" role="alert">
      <el-icon><WarningFilled /></el-icon>
      <div>
        <strong>{{ record.result.error?.message || '该分析步骤未能完成' }}</strong>
        <p>{{ record.result.error?.action || '其他已成功结果仍然保留；可检查能力状态后单独重试此步骤。' }}</p>
        <small v-if="record.result.error?.code">错误代码：{{ record.result.error.code }}</small>
      </div>
    </div>
    <div v-else-if="record.status === 'running'" class="step-state">
      <el-icon class="is-loading"><Clock /></el-icon><div><strong>该步骤仍在执行</strong><p>完成后刷新即可查看；当前页面中的其他成功结果不受影响。</p></div>
    </div>
    <div v-else-if="record.status === 'canceled'" class="step-state">
      <el-icon><Clock /></el-icon><div><strong>该步骤已取消</strong><p>取消不会移除此前已经完成的其他分析结果。</p></div>
    </div>

    <template v-else-if="record.status === 'completed'">
      <section v-if="record.result.basic" class="basic-result">
        <div class="metric-grid compact">
          <article><small>视频作者</small><strong>{{ record.result.basic.ownerName || '暂无' }}</strong></article>
          <article><small>视频时长</small><strong>{{ formatDuration(record.result.basic.durationSeconds) }}</strong></article>
          <article><small>当前分 P</small><strong>{{ record.result.basic.pageNumber ? `P${record.result.basic.pageNumber}` : '暂无' }}</strong></article>
          <article><small>字幕检查</small><strong>{{ subtitleAvailabilityLabel(record.result.basic.subtitleAvailability) }}</strong></article>
        </div>
        <h4>{{ record.result.basic.title || record.result.basic.partTitle }}</h4>
        <p v-if="record.result.basic.description" class="long-copy">{{ record.result.basic.description }}</p>
        <div v-if="record.result.basic.tags.length" class="tag-list"><el-tag v-for="tag in record.result.basic.tags" :key="tag" size="small" effect="plain">{{ tag }}</el-tag></div>
      </section>

      <section v-if="record.result.media" class="media-result">
        <div v-if="record.result.media.container" class="metric-grid compact">
          <article><small>容器</small><strong>{{ record.result.media.container.formatNames.join(' / ') || '暂无' }}</strong><span>{{ record.result.media.container.formatLongName || '' }}</span></article>
          <article><small>媒体验证时长</small><strong>{{ formatDuration(record.result.media.container.durationSeconds) }}</strong></article>
          <article><small>文件大小</small><strong>{{ formatBytes(record.result.media.container.sizeBytes) }}</strong></article>
          <article><small>总码率</small><strong>{{ formatBitrate(record.result.media.container.bitRate) }}</strong></article>
        </div>

        <div v-if="record.result.media.videoStreams.length" class="tech-group">
          <h4>视频轨道</h4>
          <article v-for="stream in record.result.media.videoStreams" :key="`video-${stream.index}`" class="tech-card">
            <div class="tech-card-title"><strong>#{{ stream.index }} · {{ stream.codecLongName || stream.codecName || '未知编码' }}</strong><el-tag size="small" effect="plain">{{ stream.hdrType || 'SDR/HDR 未知' }}</el-tag></div>
            <dl>
              <div><dt>编码 / Profile / Level</dt><dd>{{ stream.codecName || '暂无' }} / {{ stream.profile || '暂无' }} / {{ stream.level ?? '暂无' }}</dd></div>
              <div><dt>分辨率</dt><dd>{{ resolution(stream.width, stream.height) }}</dd></div>
              <div><dt>平均 / 实际帧率</dt><dd>{{ decimal(stream.averageFrameRate, 'fps', 2) }} / {{ decimal(stream.realFrameRate, 'fps', 2) }}</dd></div>
              <div><dt>码率 / 时长</dt><dd>{{ formatBitrate(stream.bitRate) }} / {{ formatDuration(stream.durationSeconds) }}</dd></div>
              <div><dt>像素格式 / 色彩范围</dt><dd>{{ stream.pixelFormat || '暂无' }} / {{ stream.colorRange || '暂无' }}</dd></div>
              <div><dt>色彩空间 / 传递 / 原色</dt><dd>{{ stream.colorSpace || '暂无' }} / {{ stream.colorTransfer || '暂无' }} / {{ stream.colorPrimaries || '暂无' }}</dd></div>
              <div><dt>关键帧</dt><dd>{{ stream.keyframes ? `${stream.keyframes.count} 个 · 平均间隔 ${decimal(stream.keyframes.averageIntervalSeconds, '秒', 2)}` : '暂无' }}</dd></div>
            </dl>
          </article>
        </div>

        <div v-if="record.result.media.audioStreams.length" class="tech-group">
          <h4>音频轨道</h4>
          <article v-for="stream in record.result.media.audioStreams" :key="`audio-${stream.index}`" class="tech-card">
            <div class="tech-card-title"><strong>#{{ stream.index }} · {{ stream.codecLongName || stream.codecName || '未知编码' }}</strong></div>
            <dl>
              <div><dt>编码 / Profile</dt><dd>{{ stream.codecName || '暂无' }} / {{ stream.profile || '暂无' }}</dd></div>
              <div><dt>采样率 / 格式</dt><dd>{{ stream.sampleRateHz === null ? '暂无' : `${stream.sampleRateHz} Hz` }} / {{ stream.sampleFormat || '暂无' }}</dd></div>
              <div><dt>声道</dt><dd>{{ stream.channels ?? '暂无' }} · {{ stream.channelLayout || '布局暂无' }}</dd></div>
              <div><dt>码率 / 时长</dt><dd>{{ formatBitrate(stream.bitRate) }} / {{ formatDuration(stream.durationSeconds) }}</dd></div>
            </dl>
          </article>
        </div>
      </section>

      <section v-if="record.result.audio" class="audio-result">
        <div class="metric-grid compact">
          <article><small>综合响度</small><strong>{{ decimal(record.result.audio.integratedLoudnessLufs, 'LUFS') }}</strong></article>
          <article><small>响度范围</small><strong>{{ decimal(record.result.audio.loudnessRangeLu, 'LU') }}</strong></article>
          <article><small>真峰值</small><strong>{{ decimal(record.result.audio.truePeakDbfs, 'dBFS') }}</strong></article>
          <article><small>静音区间</small><strong>{{ record.result.audio.silenceIntervals.length }} 段</strong></article>
          <article><small>样本峰值</small><strong>{{ decimal(record.result.audio.samplePeakDbfs, 'dBFS') }}</strong></article>
          <article><small>平均音量</small><strong>{{ decimal(record.result.audio.meanVolumeDb, 'dB') }}</strong></article>
        </div>
        <AnalysisTimelineChart
          v-if="audioSeries.length || audioIntervals.length"
          :series="audioSeries"
          :intervals="audioIntervals"
          y-unit="LUFS"
          :summary="audioSummary"
        />
        <div v-if="record.result.audio.spectrumOverview" class="spectrum-result">
          <h4>频谱概览</h4>
          <SpectrumOverviewChart
            :bands="record.result.audio.spectrumOverview.bands"
            :summary="spectrumSummary"
          />
          <p class="method-disclaimer">{{ record.result.audio.spectrumOverview.disclaimer }}</p>
        </div>
        <div v-if="record.result.audio.contentClassification" class="content-classification">
          <h4>语音 / 音乐粗粒度区段（启发式）</h4>
          <div class="disclaimer"><el-icon><WarningFilled /></el-icon><div><strong>不是精确内容或版权识别</strong><p>{{ record.result.audio.contentClassification.disclaimer }}</p></div></div>
          <AnalysisTimelineChart
            v-if="contentSeries.length"
            :series="contentSeries"
            variant="bar"
            y-unit="%"
            :summary="contentSummary"
          />
          <ol class="classification-list">
            <li v-for="segment in record.result.audio.contentClassification.segments" :key="`${segment.index}-${segment.startSeconds}`">
              <div><el-tag size="small" effect="plain">{{ audioContentLabel(segment.label) }}</el-tag><strong>{{ formatDuration(segment.startSeconds) }} – {{ formatDuration(segment.endSeconds) }}</strong><span>置信度 {{ Math.round(segment.confidence * 100) }}%</span></div>
              <p>{{ segment.explanation }}</p>
            </li>
          </ol>
          <ul class="limitation-list"><li v-for="item in record.result.audio.contentClassification.limitations" :key="item">{{ item }}</li></ul>
        </div>
        <div v-if="record.result.audio.silenceIntervals.length" class="interval-list">
          <h4>静音时间线</h4>
          <ol>
            <li v-for="(interval, index) in record.result.audio.silenceIntervals" :key="`${interval.startSeconds}-${index}`"><strong>{{ formatDuration(interval.startSeconds) }} – {{ formatDuration(interval.endSeconds) }}</strong><span>{{ decimal(interval.durationSeconds, '秒', 2) }}</span></li>
          </ol>
        </div>
      </section>

      <section v-if="record.result.scenes" class="scenes-result">
        <div class="metric-grid compact">
          <article><small>镜头数量</small><strong>{{ record.result.scenes.scenes.length }}</strong></article>
          <article><small>平均镜头长度</small><strong>{{ decimal(record.result.scenes.averageSceneLengthSeconds, '秒', 2) }}</strong></article>
          <article><small>场景密度</small><strong>{{ decimal(record.result.scenes.sceneDensityPerMinute, '个/分钟', 2) }}</strong></article>
          <article><small>关键帧</small><strong>{{ record.result.scenes.keyframes.length }} 张</strong></article>
        </div>
        <AnalysisTimelineChart
          v-if="record.result.scenes.scenes.length"
          :series="sceneSeries"
          variant="bar"
          y-unit="秒"
          :summary="sceneSummary"
        />
        <div v-if="record.result.scenes.keyframes.length" class="keyframe-list">
          <h4>关键帧定位</h4>
          <div><span v-for="keyframe in record.result.scenes.keyframes" :key="`${keyframe.index}-${keyframe.timestampSeconds}`"><strong>{{ formatDuration(keyframe.timestampSeconds) }}</strong><small>镜头 {{ keyframe.sceneIndex }} · {{ keyframe.filename }}</small></span></div>
        </div>
      </section>

      <section v-if="transcript" class="transcript-result">
        <div class="transcript-actions">
          <div>
            <strong>时间轴文本</strong>
            <small>编辑会发布新的 edited 来源版本，并重新导出四种文本格式。</small>
          </div>
          <el-button :icon="EditPen" :disabled="transcript.segments.length > 10000" data-testid="open-transcript-editor" @click="openEditor">编辑与重新导出</el-button>
        </div>
        <div v-if="transcript.editProvenance" class="edit-provenance" role="note">
          <strong>人工编辑修订 #{{ transcript.editProvenance.revision }}</strong>
          <span>来源：{{ sourceLabel(transcript.editProvenance.sourceTranscriptSource) }} · 编辑于 {{ formatDate(transcript.editProvenance.editedAt) }}</span>
        </div>
        <div class="transcript-meta">
          <span><small>来源</small><strong>{{ sourceLabel(transcript.source) }}</strong></span>
          <span><small>语言</small><strong>{{ transcript.language }}</strong></span>
          <span><small>片段</small><strong>{{ transcript.segments.length }} 条</strong></span>
          <span><small>生成时间</small><strong>{{ formatDate(transcript.generatedAt) }}</strong></span>
        </div>
        <ol class="transcript-list">
          <li v-for="segment in visibleTranscriptSegments" :key="`${segment.index}-${segment.startSeconds}`">
            <div class="timestamp"><strong>{{ formatDuration(segment.startSeconds) }} – {{ formatDuration(segment.endSeconds) }}</strong><el-tag size="small" effect="plain">{{ sourceLabel(segment.source) }}</el-tag></div>
            <p>{{ segment.text }}</p>
            <small>置信度：{{ confidenceLabel(segment.confidence) }}<template v-if="segment.evidenceId"> · 证据 {{ segment.evidenceId }}</template></small>
          </li>
        </ol>
        <el-pagination
          v-if="transcriptSegments.length > transcriptPageSize"
          v-model:current-page="transcriptPage"
          background
          layout="prev, pager, next"
          :page-size="transcriptPageSize"
          :total="transcriptSegments.length"
          aria-label="转写片段分页"
        />
      </section>

      <section v-if="record.result.summary" class="summary-result">
        <div class="disclaimer" role="note"><el-icon><WarningFilled /></el-icon><div><strong>自动分析结果，可能存在误差</strong><p>{{ record.result.summary.disclaimer }}</p></div></div>
        <div class="summary-provenance">
          <span><small>模型</small><strong>{{ record.result.summary.modelName || displayModel.name || '未标注' }}</strong></span>
          <span><small>模型版本</small><strong>{{ record.result.summary.modelVersion || displayModel.version || '未标注' }}</strong></span>
          <span><small>生成时间</small><strong>{{ formatDate(record.result.summary.generatedAt) }}</strong></span>
          <span><small>输入来源</small><strong>{{ record.result.summary.inputSources.map(sourceLabel).join(' / ') || '未标注' }}</strong></span>
          <span><small>证据覆盖</small><strong>{{ coverageLabel(record.result.summary.coverage) }}</strong></span>
          <span><small>输入摘要</small><strong>{{ record.result.summary.inputDigestSha256?.slice(0, 16) || '未标注' }}</strong></span>
          <span><small>算法</small><strong>{{ String(record.result.summary.parameters.algorithm || '未标注') }}</strong></span>
          <span><small>输入规模</small><strong>{{ record.result.summary.inputDetails.textSegmentCount ?? 0 }} 段文本 · {{ record.result.summary.inputDetails.keyframeEvidenceCount ?? 0 }} 帧</strong></span>
        </div>
        <details class="summary-parameters"><summary>查看可复现参数与完整输入统计</summary><pre>{{ JSON.stringify({ parameters: record.result.summary.parameters, inputDetails: record.result.summary.inputDetails }, null, 2) }}</pre></details>
        <div class="summary-copy"><h4>内容摘要</h4><p>{{ record.result.summary.summary || '暂无摘要文本' }}</p></div>

        <div v-if="record.result.summary.summarySentences.length" class="evidence-list">
          <h4>关键结论与证据</h4>
          <article v-for="(sentence, index) in record.result.summary.summarySentences" :key="`${index}-${sentence.text}`">
            <p>{{ sentence.text }}</p>
            <blockquote v-if="sentence.evidence"><strong>{{ evidenceLocation(sentence.evidence) }} · {{ sourceLabel(sentence.evidence.source) }}</strong><span>{{ sentence.evidence.text }}</span><small>置信度：{{ confidenceLabel(sentence.evidence.confidence) }}</small></blockquote>
          </article>
        </div>

        <div v-if="record.result.summary.keywords.length" class="keyword-list">
          <h4>关键词</h4>
          <div><el-tag v-for="keyword in record.result.summary.keywords" :key="keyword.keyword" effect="plain">{{ keyword.keyword }} · {{ keyword.occurrences }} 次</el-tag></div>
        </div>

        <div v-if="record.result.summary.topics.length" class="topic-list">
          <h4>确定性主题候选</h4>
          <div><el-tag v-for="topic in record.result.summary.topics" :key="topic.topic" effect="plain">{{ topic.topic }}</el-tag></div>
          <small>主题来自关键词统计与证据排序，不等同于生成式模型的精确语义理解。</small>
        </div>

        <div v-if="record.result.summary.chapters.length" class="chapter-list">
          <h4>自动章节</h4>
          <article v-for="chapter in record.result.summary.chapters" :key="`${chapter.index}-${chapter.startSeconds}`">
            <div><span>{{ formatDuration(chapter.startSeconds) }} – {{ formatDuration(chapter.endSeconds) }}</span><strong>{{ chapter.title }}</strong></div>
            <p>{{ chapter.summary }}</p>
            <div v-if="chapter.keywords.length" class="tag-list"><el-tag v-for="keyword in chapter.keywords" :key="keyword" size="small" effect="plain">{{ keyword }}</el-tag></div>
            <blockquote v-for="(evidence, index) in chapter.evidence" :key="`${index}-${evidence.startSeconds}`"><strong>{{ evidenceLocation(evidence) }} · {{ sourceLabel(evidence.source) }}</strong><span>{{ evidence.text }}</span><a v-if="evidence.artifactId" :href="artifactApi.contentUrl(evidence.artifactId)" download>打开关键帧证据</a></blockquote>
          </article>
        </div>

        <div v-if="record.result.summary.visualEvidence.length" class="visual-evidence-list">
          <h4>镜头 / 关键帧结构证据</h4>
          <article v-for="evidence in record.result.summary.visualEvidence.slice(0, 12)" :key="`${evidence.source}-${evidence.evidenceId}`">
            <strong>{{ evidenceLocation(evidence) }} · {{ sourceLabel(evidence.source) }}</strong>
            <span>{{ evidence.text }}</span>
            <a v-if="evidence.artifactId" :href="artifactApi.contentUrl(evidence.artifactId)" download>下载关键帧</a>
          </article>
        </div>

        <div class="semantic-grid">
          <section class="entity-list">
            <h4>人物 / 对象能力边界</h4>
            <article v-for="entity in record.result.summary.entityCandidates" :key="`${entity.category}-${entity.name}`"><strong>{{ entity.name }}</strong><span>{{ entity.category }}</span><p>{{ entity.limitation }}</p></article>
            <p v-if="!record.result.summary.entityCandidates.length">本地确定性流程未输出人物或对象候选。</p>
          </section>
          <section class="emotion-list">
            <h4>情绪措辞走向</h4>
            <ol v-if="record.result.summary.emotionTimeline.length"><li v-for="(point, index) in record.result.summary.emotionTimeline" :key="`${index}-${point.startSeconds}`"><strong>{{ formatDuration(point.startSeconds) }} · {{ point.label }}</strong><span>{{ point.evidence?.text || '证据文本暂无' }}</span></li></ol>
            <p v-else>未发现足够明确的情绪措辞，因此没有推断“中性”或人物真实情绪。</p>
          </section>
        </div>

        <div class="capability-list">
          <h4>本次语义能力说明</h4>
          <article v-for="capability in record.result.summary.semanticCapabilities" :key="capability.name"><div><strong>{{ capability.name }}</strong><el-tag size="small" effect="plain">{{ capabilityLabel(capability.status) }}</el-tag></div><p>{{ capability.message }}</p><small>方法：{{ capability.method }}</small></article>
        </div>
      </section>

      <div v-if="!completedResultAvailable" class="step-state is-failed" role="alert">
        <el-icon><WarningFilled /></el-icon><div><strong>报告结构暂时无法识别</strong><p>记录已完成但没有可展示的结构化字段；可从产物入口下载原始报告。</p></div>
      </div>

      <div v-if="warnings.length" class="warning-list" role="note">
        <strong>准确性与处理提示</strong><ul><li v-for="warning in warnings" :key="warning">{{ warning }}</li></ul>
      </div>
    </template>

    <footer v-if="record.status === 'completed' || record.result.artifactIds.length" class="record-footer">
      <div class="model-info"><span>模型 / 工具</span><strong>{{ displayModel.name || '未标注' }}<template v-if="displayModel.version"> · {{ displayModel.version }}</template></strong></div>
      <div v-if="record.result.artifactIds.length" class="export-actions">
        <a v-for="(artifactId, index) in exportIds" :key="artifactId" :href="artifactApi.contentUrl(artifactId)" download><el-icon><Download /></el-icon>导出 {{ index + 1 }}</a>
        <RouterLink v-if="record.jobId" :to="{ name: 'artifacts', query: { jobId: record.jobId } }"><el-icon><FolderOpened /></el-icon>查看全部 {{ record.result.artifactIds.length }} 项产物</RouterLink>
      </div>
    </footer>
    <TranscriptEditDrawer
      v-if="transcript"
      v-model="editOpen"
      :transcript="transcript"
      :saving="editSaving"
      :error-message="editErrorMessage"
      @save="saveEdit"
    />
  </article>
</template>

<style scoped>
.analysis-record { min-width: 0; padding: 20px; border: 1px solid var(--line-soft); border-radius: 16px; background: var(--surface); }
.record-header, .record-header > div, .tech-card-title, .timestamp, .record-footer, .export-actions { display: flex; align-items: center; }
.record-header { justify-content: space-between; gap: 16px; padding-bottom: 15px; border-bottom: 1px solid var(--line-soft); }
.record-header > div { min-width: 0; gap: 11px; }
.record-header h3, .record-header small { display: block; margin: 0; }
.record-header h3 { font-size: 16px; }
.record-header small { margin-top: 3px; color: var(--text-tertiary); font-size: 10px; }
.level { display: grid; place-items: center; flex: 0 0 auto; width: 36px; height: 36px; border-radius: 10px; background: var(--brand-soft); color: var(--brand); font-size: 11px; font-weight: 750; }
.step-state { display: flex; align-items: flex-start; gap: 11px; margin-top: 16px; padding: 14px; border-radius: 11px; background: var(--surface-muted); }
.step-state > .el-icon { flex: 0 0 auto; margin-top: 2px; font-size: 18px; }
.step-state strong, .step-state p, .step-state small { display: block; }
.step-state p { margin: 4px 0 0; color: var(--text-secondary); line-height: 1.55; }
.step-state small { margin-top: 6px; color: var(--text-tertiary); }
.step-state.is-failed { border: 1px solid #efc1b8; background: #fff4f1; color: #8c2b1e; }
.step-state.is-failed p, .step-state.is-failed small { color: #9f5145; }
.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 9px; margin-top: 16px; }
.metric-grid article { min-width: 0; padding: 13px; border-radius: 11px; background: var(--surface-muted); }
.metric-grid small, .metric-grid strong, .metric-grid span { display: block; overflow-wrap: anywhere; }
.metric-grid small { color: var(--text-tertiary); font-size: 10px; }
.metric-grid strong { margin-top: 6px; font-size: 14px; }
.metric-grid span { margin-top: 3px; color: var(--text-secondary); font-size: 10px; }
.basic-result h4, .tech-group h4, .interval-list h4, .keyframe-list h4, .summary-result h4, .spectrum-result h4, .content-classification h4, .topic-list h4, .entity-list h4, .emotion-list h4, .capability-list h4, .visual-evidence-list h4 { margin: 20px 0 9px; font-size: 13px; }
.long-copy, .summary-copy p { margin: 0; color: var(--text-secondary); line-height: 1.75; white-space: pre-wrap; overflow-wrap: anywhere; }
.tag-list, .keyword-list > div { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.tech-group { margin-top: 18px; }
.tech-card { margin-top: 9px; padding: 14px; border: 1px solid var(--line-soft); border-radius: 12px; }
.tech-card-title { justify-content: space-between; gap: 10px; }
.tech-card dl { display: grid; grid-template-columns: 1fr 1fr; gap: 0 20px; margin: 10px 0 0; }
.tech-card dl div { display: grid; grid-template-columns: minmax(120px, .7fr) 1fr; gap: 10px; padding: 9px 0; border-top: 1px solid var(--line-soft); }
.tech-card dt { color: var(--text-tertiary); font-size: 11px; }
.tech-card dd { min-width: 0; margin: 0; font-size: 11px; overflow-wrap: anywhere; }
.audio-result :deep(.analysis-chart), .scenes-result :deep(.analysis-chart) { margin-top: 16px; }
.spectrum-result :deep(.spectrum-chart), .content-classification :deep(.analysis-chart) { margin-top: 12px; }
.method-disclaimer { margin: 9px 0 0; color: var(--text-tertiary); font-size: 10px; line-height: 1.55; }
.classification-list { display: grid; gap: 8px; margin: 12px 0 0; padding: 0; list-style: none; }
.classification-list li { padding: 11px 12px; border: 1px solid var(--line-soft); border-radius: 10px; }
.classification-list li > div { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.classification-list strong { font-size: 11px; }
.classification-list span { color: var(--text-tertiary); font-size: 10px; }
.classification-list p { margin: 7px 0 0; color: var(--text-secondary); font-size: 10px; line-height: 1.55; }
.limitation-list { margin: 10px 0 0; padding: 11px 11px 11px 28px; border-radius: 10px; background: var(--surface-muted); color: var(--text-secondary); font-size: 10px; line-height: 1.6; }
.interval-list ol { display: grid; grid-template-columns: repeat(2, 1fr); gap: 7px; padding: 0; list-style: none; }
.interval-list li { display: flex; justify-content: space-between; gap: 12px; padding: 10px 12px; border-radius: 9px; background: var(--surface-muted); font-size: 11px; }
.interval-list li span { color: var(--text-secondary); }
.keyframe-list > div { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 7px; }
.keyframe-list span { min-width: 0; padding: 10px; border-radius: 9px; background: var(--surface-muted); }
.keyframe-list strong, .keyframe-list small { display: block; overflow-wrap: anywhere; }
.keyframe-list small { margin-top: 3px; color: var(--text-tertiary); font-size: 9px; }
.transcript-meta { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-top: 16px; }
.transcript-meta span { min-width: 0; padding: 11px; border-radius: 9px; background: var(--surface-muted); }
.transcript-meta small, .transcript-meta strong { display: block; overflow-wrap: anywhere; }
.transcript-meta small { color: var(--text-tertiary); font-size: 9px; }
.transcript-meta strong { margin-top: 4px; font-size: 11px; }
.transcript-list { display: grid; gap: 8px; padding: 0; list-style: none; }
.transcript-list li { padding: 13px; border: 1px solid var(--line-soft); border-radius: 11px; }
.timestamp { justify-content: space-between; gap: 10px; }
.timestamp strong { color: var(--brand); font-size: 11px; }
.transcript-list p { margin: 10px 0 7px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
.transcript-list li > small { color: var(--text-tertiary); }
.transcript-result .el-pagination { justify-content: center; margin-top: 14px; }
.transcript-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 16px; padding: 12px; border-radius: 11px; background: var(--surface-muted); }
.transcript-actions strong, .transcript-actions small { display: block; }
.transcript-actions small { margin-top: 4px; color: var(--text-tertiary); font-size: 10px; line-height: 1.5; }
.transcript-actions .el-button { flex: 0 0 auto; min-height: 44px; }
.edit-provenance { display: grid; gap: 4px; margin-top: 10px; padding: 11px 12px; border-radius: 10px; background: var(--brand-soft); color: var(--brand); font-size: 10px; }
.edit-provenance span { color: var(--text-secondary); }
.disclaimer { display: flex; align-items: flex-start; gap: 10px; margin-top: 16px; padding: 13px; border-radius: 11px; background: #fff6e9; color: #95521b; }
.disclaimer > .el-icon { flex: 0 0 auto; margin-top: 2px; }
.disclaimer p { margin: 4px 0 0; line-height: 1.55; }
.summary-provenance { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-top: 10px; }
.summary-provenance span { min-width: 0; padding: 10px; border-radius: 9px; background: var(--surface-muted); }
.summary-provenance small, .summary-provenance strong { display: block; overflow-wrap: anywhere; }
.summary-provenance small { color: var(--text-tertiary); font-size: 9px; }
.summary-provenance strong { margin-top: 4px; font-size: 11px; }
.summary-parameters { margin-top: 10px; color: var(--text-secondary); font-size: 10px; }
.summary-parameters summary { cursor: pointer; min-height: 44px; padding: 10px 0; }
.summary-parameters pre { max-height: 220px; margin: 0; padding: 10px; overflow: auto; border-radius: 9px; background: var(--surface-muted); white-space: pre-wrap; overflow-wrap: anywhere; }
.evidence-list article, .chapter-list > article { margin-top: 9px; padding: 13px; border: 1px solid var(--line-soft); border-radius: 11px; }
.evidence-list article > p, .chapter-list p { margin: 0; line-height: 1.65; }
blockquote { display: grid; gap: 4px; margin: 10px 0 0; padding: 10px 12px; border-left: 3px solid var(--brand); border-radius: 0 9px 9px 0; background: var(--surface-muted); color: var(--text-secondary); font-size: 11px; overflow-wrap: anywhere; }
blockquote strong { color: var(--brand); }
blockquote small { color: var(--text-tertiary); }
.keyword-list { margin-top: 18px; }
.chapter-list { margin-top: 18px; }
.chapter-list > article > div:first-child { display: grid; gap: 4px; }
.chapter-list > article > div:first-child span { color: var(--brand); font-size: 10px; }
.topic-list, .visual-evidence-list, .semantic-grid, .capability-list { margin-top: 18px; }
.topic-list > div { display: flex; flex-wrap: wrap; gap: 6px; }
.topic-list > small { display: block; margin-top: 8px; color: var(--text-tertiary); font-size: 10px; line-height: 1.55; }
.semantic-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.entity-list article, .emotion-list li, .capability-list article, .visual-evidence-list article { margin-top: 8px; padding: 11px 12px; border: 1px solid var(--line-soft); border-radius: 10px; background: var(--surface-muted); }
.entity-list article strong, .entity-list article span, .entity-list article p { display: block; }
.entity-list article span { margin-top: 3px; color: var(--brand); font-size: 10px; }
.entity-list article p, .entity-list > p, .emotion-list > p, .capability-list p { margin: 6px 0 0; color: var(--text-secondary); font-size: 10px; line-height: 1.55; }
.emotion-list ol { display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; }
.emotion-list li strong, .emotion-list li span { display: block; }
.emotion-list li strong { color: var(--brand); font-size: 10px; }
.emotion-list li span { margin-top: 5px; color: var(--text-secondary); font-size: 10px; }
.capability-list article > div { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.capability-list article p { margin-top: 7px; }
.capability-list article small { display: block; margin-top: 6px; color: var(--text-tertiary); font-size: 9px; }
.visual-evidence-list article { display: grid; gap: 5px; }
.visual-evidence-list article strong { color: var(--brand); font-size: 10px; }
.visual-evidence-list article span { color: var(--text-secondary); font-size: 10px; }
.visual-evidence-list article a, blockquote a { color: var(--brand); font-size: 10px; }
.warning-list { margin-top: 16px; padding: 13px; border-radius: 10px; background: #fff6e9; color: #95521b; }
.warning-list ul { margin: 6px 0 0; padding-left: 18px; line-height: 1.6; }
.record-footer { justify-content: space-between; gap: 16px; margin-top: 18px; padding-top: 14px; border-top: 1px solid var(--line-soft); }
.model-info { min-width: 0; }
.model-info span, .model-info strong { display: block; overflow-wrap: anywhere; }
.model-info span { color: var(--text-tertiary); font-size: 9px; }
.model-info strong { margin-top: 3px; font-size: 11px; }
.export-actions { justify-content: flex-end; flex-wrap: wrap; gap: 6px; }
.export-actions a { display: inline-flex; align-items: center; justify-content: center; gap: 5px; min-height: 44px; padding: 0 11px; border: 1px solid var(--line); border-radius: 9px; color: var(--brand); font-size: 11px; text-decoration: none; }
@media (max-width: 900px) {
  .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .tech-card dl { grid-template-columns: 1fr; }
}
@media (max-width: 767px) {
  .analysis-record { padding: 14px; }
  .record-header { align-items: flex-start; }
  .record-header h3 { font-size: 14px; }
  .tech-card dl div { grid-template-columns: 1fr; gap: 4px; }
  .interval-list ol, .keyframe-list > div { grid-template-columns: 1fr; }
  .transcript-meta { grid-template-columns: repeat(2, 1fr); }
  .summary-provenance { grid-template-columns: repeat(2, 1fr); }
  .transcript-actions { align-items: flex-start; flex-direction: column; }
  .transcript-actions .el-button { width: 100%; }
  .semantic-grid { grid-template-columns: 1fr; }
  .record-footer { align-items: flex-start; flex-direction: column; }
  .export-actions { justify-content: flex-start; width: 100%; }
  .export-actions a { flex: 1 1 calc(50% - 6px); min-width: 0; text-align: center; overflow-wrap: anywhere; }
}
@media (max-width: 374px) {
  .metric-grid { grid-template-columns: 1fr; }
  .record-header > div { gap: 7px; }
  .level { width: 32px; height: 32px; }
}
</style>
