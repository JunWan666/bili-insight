<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  ArrowLeft,
  CircleCheck,
  Connection,
  Cpu,
  Document,
  Download,
  Files,
  Film,
  Refresh,
  Timer,
  Warning,
} from '@element-plus/icons-vue'
import { diagnosticsApi } from '@/api'
import { toApiError, type ApiError } from '@/api/errors'
import PageHeader from '@/components/PageHeader.vue'
import RequestError from '@/components/RequestError.vue'
import type { ComponentHealth, Diagnostics, HealthComponent } from '@/types/api'
import { formatBytes, formatDate, formatDuration } from '@/utils/format'

const diagnostics = ref<Diagnostics | null>(null)
const loading = ref(false)
const exporting = ref(false)
const error = ref<ApiError | null>(null)
const diagnosticsDisabled = computed(() => error.value?.code === 'DIAGNOSTICS_DISABLED')

const healthView: Record<ComponentHealth, { label: string; type: 'success' | 'warning' | 'danger'; icon: typeof CircleCheck }> = {
  healthy: { label: '正常', type: 'success', icon: CircleCheck },
  degraded: { label: '能力受限', type: 'warning', icon: Warning },
  unavailable: { label: '不可用', type: 'danger', icon: Warning },
}

const iconForComponent = (component: HealthComponent) => {
  if (/ffmpeg|ffprobe/i.test(component.name)) return Film
  if (/worker|queue/i.test(component.name)) return Timer
  if (/model|asr|ocr/i.test(component.name)) return Cpu
  if (/database|storage|disk/i.test(component.name)) return Files
  return Connection
}

const uptime = computed(() => {
  if (!diagnostics.value?.startedAt) return '暂无'
  const seconds = (Date.now() - new Date(diagnostics.value.startedAt).getTime()) / 1000
  return Number.isFinite(seconds) && seconds >= 0 ? formatDuration(seconds) : '暂无'
})

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try { diagnostics.value = await diagnosticsApi.get() }
  catch (reason) {
    error.value = toApiError(reason)
    if (error.value.code === 'DIAGNOSTICS_DISABLED') diagnostics.value = null
  }
  finally { loading.value = false }
}

async function exportReport(): Promise<void> {
  exporting.value = true
  try {
    const blob = await diagnosticsApi.downloadReport()
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `biliscope-diagnostics-${new Date().toISOString().slice(0, 10)}.json`
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
    ElMessage.success('脱敏诊断报告已导出')
  } catch (reason) { ElMessage.error(toApiError(reason).message) }
  finally { exporting.value = false }
}

onMounted(() => void load())
</script>

<template>
  <div class="diagnostics-view">
    <PageHeader title="关于与诊断" description="检查应用、媒体工具、分析模型、Worker、队列和磁盘的实际运行状态。" eyebrow="SYSTEM HEALTH">
      <template #actions><el-button :icon="Refresh" :loading="loading" @click="load">重新检查</el-button><el-button type="primary" plain :icon="Download" :loading="exporting" :disabled="!diagnostics" @click="exportReport">导出脱敏诊断</el-button></template>
    </PageHeader>

    <section v-if="diagnosticsDisabled && !diagnostics" class="diagnostics-disabled surface-card" role="status">
      <el-icon><Warning /></el-icon>
      <div><h2>详细诊断已关闭</h2><p>组件版本、队列和磁盘指标不会被采集或导出；服务的基础健康检查仍保持可用。</p><RouterLink to="/settings">前往隐私设置启用诊断</RouterLink></div>
    </section>
    <RequestError v-else-if="error && !diagnostics" :error="error" @retry="load" />

    <template v-if="diagnostics">
      <section class="overview surface-card">
        <span class="app-mark"><Film /></span>
        <div><p>BILISCOPE</p><h2>本地视频工作台</h2><span>版本 {{ diagnostics.applicationVersion }} · {{ diagnostics.environment }}</span></div>
        <el-tag size="large" effect="plain" :type="healthView[diagnostics.status].type"><el-icon><component :is="healthView[diagnostics.status].icon" /></el-icon>{{ healthView[diagnostics.status].label }}</el-tag>
        <dl><div><dt>启动时间</dt><dd>{{ formatDate(diagnostics.startedAt) }}</dd></div><div><dt>已运行</dt><dd>{{ uptime }}</dd></div><div><dt>请求编号</dt><dd>{{ diagnostics.requestId || '暂无' }}</dd></div></dl>
      </section>

      <section class="health-section">
        <div class="section-heading"><div><h2>组件健康</h2><p>模型未安装时不影响基础解析和下载；相应分析能力会明确标为不可用。</p></div></div>
        <div class="component-grid">
          <article v-for="component in diagnostics.components" :key="component.name" class="surface-card">
            <span class="component-icon"><el-icon><component :is="iconForComponent(component)" /></el-icon></span>
            <div><h3>{{ component.name }}</h3><p>{{ component.message || '组件运行正常' }}</p><small>{{ component.version ? `版本 ${component.version}` : '未报告版本' }}</small></div>
            <el-tag size="small" effect="plain" :type="healthView[component.status].type">{{ healthView[component.status].label }}</el-tag>
          </article>
        </div>
      </section>

      <section class="metrics-grid">
        <article class="surface-card"><div class="metric-title"><span><Files /></span><div><h2>磁盘空间</h2><p>产物与临时目录的服务端统计</p></div></div><div class="disk-values"><strong>{{ formatBytes(diagnostics.disk.freeBytes) }}</strong><span>可用 / {{ formatBytes(diagnostics.disk.totalBytes) }}</span></div><el-progress :percentage="diagnostics.disk.totalBytes ? Math.round(diagnostics.disk.usedBytes / diagnostics.disk.totalBytes * 100) : 0" :stroke-width="9" :show-text="false" /><dl><div><dt>产物</dt><dd>{{ formatBytes(diagnostics.disk.artifactBytes) }}</dd></div><div><dt>临时文件</dt><dd>{{ formatBytes(diagnostics.disk.temporaryBytes) }}</dd></div></dl></article>
        <article class="surface-card"><div class="metric-title"><span><Timer /></span><div><h2>任务队列</h2><p>当前调度和近 24 小时失败情况</p></div></div><div class="queue-grid"><div><strong>{{ diagnostics.queue.queued }}</strong><small>等待中</small></div><div><strong>{{ diagnostics.queue.running }}</strong><small>执行中</small></div><div :class="{ danger: diagnostics.queue.failedLast24Hours }"><strong>{{ diagnostics.queue.failedLast24Hours }}</strong><small>24h 失败</small></div></div><RouterLink class="metric-link" to="/jobs">打开任务中心 <el-icon><ArrowLeft /></el-icon></RouterLink></article>
      </section>

      <section class="diagnostic-privacy surface-card"><el-icon><Document /></el-icon><div><h2>诊断数据已脱敏</h2><p>导出内容不包含 Cookie、CSRF、签名媒体 URL、账号标识或服务器绝对路径。请不要在问题反馈中附加原始 Cookie 文件。</p></div></section>

      <section class="about surface-card"><h2>使用边界</h2><p>本工具用于处理你有权访问和使用的内容，不会绕过付费、DRM、验证码或平台访问控制。下载与使用行为应遵守 Bilibili 平台条款及适用的版权法律。</p></section>
    </template>

    <div v-else-if="loading" class="diagnostics-loading surface-card"><el-skeleton :rows="10" animated /></div>
  </div>
</template>

<style scoped>
.diagnostics-view { max-width: 1120px; margin: 0 auto; }.overview { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 16px; padding: 22px; }.app-mark { display: grid; place-items: center; width: 55px; height: 55px; border-radius: 17px; background: var(--brand); color: white; box-shadow: 0 10px 22px rgba(70, 90, 203, .24); }.app-mark svg { width: 27px; }.overview p, .overview h2, .overview span { margin: 0; }.overview p { color: var(--brand); font-size: 9px; font-weight: 800; letter-spacing: .16em; }.overview h2 { margin: 4px 0; font-size: 19px; }.overview > div > span { color: var(--text-tertiary); font-size: 11px; }.overview dl { grid-column: 2 / -1; display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin: 3px 0 0; padding-top: 16px; border-top: 1px solid var(--line-soft); }.overview dt { color: var(--text-tertiary); font-size: 9px; }.overview dd { margin: 4px 0 0; color: var(--text-secondary); font-size: 11px; overflow-wrap: anywhere; }
.health-section { margin-top: 30px; }.section-heading h2 { margin: 0; font-size: 21px; }.section-heading p { margin: 5px 0 15px; color: var(--text-secondary); }.component-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 11px; }.component-grid article { display: grid; grid-template-columns: auto 1fr auto; align-items: start; gap: 12px; padding: 16px; }.component-icon { display: grid; place-items: center; width: 39px; height: 39px; border-radius: 11px; background: var(--surface-muted); color: var(--brand); }.component-icon .el-icon { font-size: 20px; }.component-grid h3, .component-grid p { margin: 0; }.component-grid h3 { font-size: 14px; }.component-grid p { margin-top: 4px; color: var(--text-secondary); font-size: 10px; line-height: 1.5; }.component-grid small { display: block; margin-top: 6px; color: var(--text-tertiary); font-size: 9px; }
.metrics-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 28px; }.metrics-grid > article { padding: 20px; }.metric-title { display: flex; align-items: center; gap: 10px; }.metric-title > span { display: grid; place-items: center; width: 38px; height: 38px; border-radius: 10px; background: var(--brand-soft); color: var(--brand); }.metric-title svg { width: 19px; }.metric-title h2, .metric-title p { margin: 0; }.metric-title h2 { font-size: 15px; }.metric-title p { margin-top: 3px; color: var(--text-tertiary); font-size: 10px; }.disk-values { margin: 22px 0 10px; }.disk-values strong { font-size: 28px; }.disk-values span { color: var(--text-tertiary); }.metrics-grid dl { display: flex; gap: 30px; margin: 15px 0 0; }.metrics-grid dt { color: var(--text-tertiary); font-size: 9px; }.metrics-grid dd { margin: 3px 0 0; font-size: 11px; }.queue-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 24px; }.queue-grid div { padding: 13px; border-radius: 11px; background: var(--surface-muted); }.queue-grid strong, .queue-grid small { display: block; }.queue-grid strong { font-size: 22px; }.queue-grid small { color: var(--text-tertiary); font-size: 9px; }.queue-grid .danger strong { color: var(--danger); }.metric-link { display: flex; align-items: center; gap: 6px; margin-top: 17px; font-size: 11px; font-weight: 650; text-decoration: none; }.metric-link .el-icon { transform: rotate(180deg); }
.diagnostic-privacy { display: flex; align-items: flex-start; gap: 12px; margin-top: 12px; padding: 17px; background: var(--brand-soft); color: var(--brand); }.diagnostic-privacy > .el-icon { font-size: 23px; }.diagnostic-privacy h2, .diagnostic-privacy p { margin: 0; }.diagnostic-privacy h2 { font-size: 13px; }.diagnostic-privacy p { margin-top: 4px; color: var(--text-secondary); font-size: 11px; line-height: 1.6; }.about { margin-top: 12px; padding: 20px; }.about h2 { margin: 0 0 7px; font-size: 14px; }.about p { margin: 0; color: var(--text-secondary); font-size: 11px; line-height: 1.7; }.diagnostics-loading { padding: 28px; }
.diagnostics-disabled { display: flex; align-items: flex-start; gap: 14px; padding: 22px; }.diagnostics-disabled > .el-icon { margin-top: 2px; color: var(--warning); font-size: 24px; }.diagnostics-disabled h2, .diagnostics-disabled p { margin: 0; }.diagnostics-disabled h2 { font-size: 16px; }.diagnostics-disabled p { margin: 7px 0 12px; color: var(--text-secondary); line-height: 1.6; }.diagnostics-disabled a { color: var(--brand); font-weight: 650; text-decoration: none; }
@media (max-width: 767px) { .overview { grid-template-columns: auto 1fr; padding: 17px; }.overview > .el-tag { grid-column: 1 / -1; justify-self: start; }.overview dl { grid-column: 1 / -1; grid-template-columns: 1fr; }.component-grid, .metrics-grid { grid-template-columns: 1fr; }.component-grid article { grid-template-columns: auto 1fr; }.component-grid article > .el-tag { grid-column: 2; justify-self: start; }.queue-grid { gap: 6px; }.queue-grid div { padding: 10px; } }
</style>
