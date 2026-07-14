<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ArrowRight,
  Check,
  CircleClose,
  Clock,
  Delete,
  DocumentCopy,
  Files,
  Film,
  Key,
  Lock,
  MagicStick,
  VideoPlay,
} from '@element-plus/icons-vue'
import AuthStatusBadge from '@/components/AuthStatusBadge.vue'
import RequestError from '@/components/RequestError.vue'
import { useAuthStore } from '@/stores/auth'
import { useVideosStore } from '@/stores/videos'
import type { AccessMode } from '@/types/api'
import { formatDate, formatDuration } from '@/utils/format'
import { normalizeVideoInput, VideoInputError } from '@/utils/videoUrl'

const router = useRouter()
const auth = useAuthStore()
const videos = useVideosStore()
const input = ref('')
const accessMode = ref<AccessMode>('auto')
const validationMessage = ref('')

const modes: Array<{ value: AccessMode; title: string; description: string }> = [
  { value: 'auto', title: '自动', description: '先匿名解析，可手动补充登录画质' },
  { value: 'anonymous', title: '仅匿名', description: '本次全程不使用已保存 Cookie' },
  { value: 'authenticated', title: '使用登录态', description: '明确使用当前已校验的登录权益' },
]

const canUseAuthenticated = computed(() => auth.isAuthenticated)

async function pasteFromClipboard(): Promise<void> {
  try {
    input.value = await navigator.clipboard.readText()
    validationMessage.value = ''
  } catch {
    ElMessage.warning('浏览器未授予剪贴板权限，请手动粘贴链接')
  }
}

function clearInput(): void {
  input.value = ''
  validationMessage.value = ''
}

async function parseVideo(): Promise<void> {
  validationMessage.value = ''
  let normalized: ReturnType<typeof normalizeVideoInput>
  try {
    normalized = normalizeVideoInput(input.value)
  } catch (reason) {
    validationMessage.value = reason instanceof VideoInputError ? reason.message : '无法识别该链接'
    return
  }

  if (accessMode.value === 'authenticated' && !canUseAuthenticated.value) {
    validationMessage.value = '当前没有有效登录态，请先上传并校验 Cookie，或选择匿名解析'
    return
  }

  input.value = normalized.url
  try {
    const video = await videos.parse(normalized.url, accessMode.value)
    await router.push({ name: 'video-detail', params: { videoId: video.id } })
  } catch {
    // Store exposes a safe, actionable error below the form.
  }
}

function selectMode(mode: AccessMode): void {
  accessMode.value = mode
  validationMessage.value = ''
}

onMounted(() => {
  void videos.loadRecent()
})
</script>

<template>
  <div class="home-view">
    <section class="hero">
      <div class="hero-copy">
        <p class="eyebrow">LOCAL MEDIA WORKBENCH</p>
        <h1>从一个链接开始，<br><span>看清视频的每一层。</span></h1>
        <p class="lead">解析实际可访问的音视频规格，按需下载、合并并生成可追踪的内容与媒体分析。</p>
      </div>

      <div class="parse-panel surface-card">
        <div class="panel-heading">
          <div>
            <span class="step-label">新建解析</span>
            <h2>粘贴 Bilibili 视频链接</h2>
          </div>
          <AuthStatusBadge :status="auth.status" :loading="auth.loading" />
        </div>

        <form novalidate @submit.prevent="parseVideo">
          <label class="sr-only" for="video-url">Bilibili 视频链接、BV/AV 号或 ss/ep 标识</label>
          <div class="input-row" :class="{ invalid: validationMessage }">
            <el-input
              id="video-url"
              v-model="input"
              data-testid="video-url-input"
              size="large"
              clearable
              placeholder="BV/AV 投稿链接或 bangumi/play/ss..."
              autocomplete="off"
              @input="validationMessage = ''"
              @keyup.enter="parseVideo"
            >
              <template #prefix><el-icon><Film /></el-icon></template>
            </el-input>
            <el-button class="paste-button" size="large" :icon="DocumentCopy" @click="pasteFromClipboard">粘贴</el-button>
            <el-button v-if="input" class="clear-button" size="large" :icon="Delete" aria-label="清空链接" @click="clearInput" />
          </div>
          <p v-if="validationMessage" class="validation" role="alert"><el-icon><CircleClose /></el-icon>{{ validationMessage }}</p>

          <fieldset class="mode-fieldset">
            <legend>本次解析身份</legend>
            <div class="mode-options">
              <button
                v-for="mode in modes"
                :key="mode.value"
                type="button"
                :data-testid="`access-mode-${mode.value}`"
                :class="{ selected: accessMode === mode.value, disabled: mode.value === 'authenticated' && !canUseAuthenticated }"
                :aria-pressed="accessMode === mode.value"
                @click="selectMode(mode.value)"
              >
                <span class="radio-mark"><Check /></span>
                <span><strong>{{ mode.title }}</strong><small>{{ mode.description }}</small></span>
              </button>
            </div>
          </fieldset>

          <div v-if="auth.isAuthenticated && accessMode !== 'authenticated'" class="auth-explanation">
            <el-icon><Lock /></el-icon>
            <span>系统已保存有效登录态，但本次首次解析不会携带 Cookie。</span>
          </div>

          <el-button class="parse-button" native-type="submit" type="primary" size="large" :loading="videos.loading" data-testid="parse-submit">
            {{ videos.loading ? '正在安全解析…' : '开始解析' }}
            <el-icon v-if="!videos.loading"><ArrowRight /></el-icon>
          </el-button>
        </form>

        <RequestError v-if="videos.error" class="parse-error" :error="videos.error" @retry="parseVideo" />
      </div>
    </section>

    <section v-if="videos.recent.length" class="recent-section">
      <div class="section-heading">
        <div><h2>最近解析</h2><p>继续查看此前解析的视频和分 P。</p></div>
      </div>
      <div class="recent-grid">
        <RouterLink v-for="video in videos.recent" :key="video.id" class="recent-card surface-card" :to="`/videos/${video.id}`">
          <div class="recent-cover">
            <img :src="video.coverUrl" :alt="`${video.title} 封面`" loading="lazy" referrerpolicy="no-referrer" />
            <span>{{ formatDuration(video.duration) }}</span>
          </div>
          <div class="recent-body">
            <h3>{{ video.title }}</h3>
            <p>{{ video.ownerName }}</p>
            <small><el-icon><Clock /></el-icon>{{ formatDate(video.parsedAt) }}</small>
          </div>
        </RouterLink>
      </div>
    </section>

    <section class="capabilities">
      <div class="section-heading">
        <div><span class="step-label">工作流</span><h2>解析之后，你可以继续</h2><p>所有耗时操作进入任务队列，进度、失败原因和产物都可追踪。</p></div>
      </div>
      <div class="capability-grid">
        <article><span><VideoPlay /></span><h3>选择真实媒体流</h3><p>对比分辨率、编码、帧率、码率、音频与预估体积，区分登录权益和视频源能力。</p></article>
        <article><span><Files /></span><h3>下载与无损合并</h3><p>刷新临时地址后下载 DASH 音视频，按兼容策略封装，并由 FFprobe 验证最终产物。</p></article>
        <article><span><MagicStick /></span><h3>按需分析内容</h3><p>独立选择媒体分析、字幕、ASR、OCR、镜头与摘要，不为不需要的模型付出时间。</p></article>
      </div>
    </section>

    <section class="support-note surface-card">
      <div class="support-icon"><Key /></div>
      <div><h2>支持范围与使用边界</h2><p>当前支持普通 BV/AV 投稿、分 P，以及番剧 ss/ep Season 与剧集链接。工具不会绕过付费、DRM、验证码或平台访问控制；请仅处理你有权访问和使用的内容，并遵守平台条款与版权法律。</p></div>
      <RouterLink to="/settings">管理 Cookie <el-icon><ArrowRight /></el-icon></RouterLink>
    </section>
  </div>
</template>

<style scoped>
.home-view { width: 100%; }
.hero { display: grid; grid-template-columns: minmax(290px, .82fr) minmax(500px, 1.18fr); align-items: center; gap: clamp(32px, 4vw, 64px); min-height: 0; padding: 10px 0 28px; }
.eyebrow, .step-label { color: var(--brand); font-size: 11px; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; }
.hero-copy h1 { margin: 12px 0 18px; font-size: 52px; line-height: 1.08; letter-spacing: 0; }
.hero-copy h1 span { color: var(--brand); }
.lead { max-width: 580px; margin: 0; color: var(--text-secondary); font-size: 15px; line-height: 1.65; }
.parse-panel { padding: 26px; }
.panel-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 19px; }
.panel-heading h2 { margin: 7px 0 0; font-size: 23px; letter-spacing: -.03em; }
.input-row { display: flex; gap: 8px; }
.input-row :deep(.el-input__wrapper) { min-height: 52px; }
.input-row.invalid :deep(.el-input__wrapper) { box-shadow: 0 0 0 1px var(--danger) inset; }
.paste-button { min-width: 92px; }
.clear-button { display: none; }
.validation { display: flex; align-items: center; gap: 7px; margin: 9px 0 0; color: var(--danger); line-height: 1.45; }
.mode-fieldset { padding: 0; margin: 19px 0 0; border: 0; }
.mode-fieldset legend { margin-bottom: 11px; color: var(--text-secondary); font-size: 13px; font-weight: 700; }
.mode-options { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.mode-options button { display: flex; align-items: flex-start; gap: 9px; min-height: 82px; padding: 12px; border: 1px solid var(--line); border-radius: 13px; background: var(--surface); color: var(--text-primary); text-align: left; cursor: pointer; transition: border-color .16s, background .16s; }
.mode-options button.selected { border-color: var(--brand); background: var(--brand-soft); }
.mode-options button.disabled:not(.selected) { opacity: .62; }
.radio-mark { display: grid; place-items: center; flex: 0 0 auto; width: 19px; height: 19px; margin-top: 1px; border: 1.5px solid var(--line); border-radius: 50%; color: transparent; }
.selected .radio-mark { border-color: var(--brand); background: var(--brand); color: white; }
.radio-mark svg { width: 12px; }
.mode-options strong, .mode-options small { display: block; }
.mode-options strong { margin-bottom: 5px; font-size: 13px; }
.mode-options small { color: var(--text-tertiary); line-height: 1.4; }
.auth-explanation { display: flex; align-items: center; gap: 8px; margin-top: 14px; padding: 10px 12px; border-radius: 10px; background: var(--surface-muted); color: var(--text-secondary); font-size: 12px; line-height: 1.45; }
.parse-button { width: 100%; min-height: 48px; margin-top: 15px; }
.parse-error { margin-top: 16px; }
.recent-section { padding: 20px 0 28px; }
.capabilities { padding: 32px 0; }
.section-heading { display: flex; justify-content: space-between; gap: 24px; margin-bottom: 16px; }
.section-heading h2 { margin: 4px 0 0; font-size: 24px; letter-spacing: 0; }
.section-heading p { margin: 5px 0 0; color: var(--text-secondary); }
.recent-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }
.recent-card { overflow: hidden; color: inherit; text-decoration: none; transition: transform .16s, box-shadow .16s; }
.recent-card:hover { transform: translateY(-2px); box-shadow: 0 11px 30px rgba(31, 36, 51, .1); }
.recent-cover { position: relative; aspect-ratio: 16 / 10; overflow: hidden; background: var(--surface-muted); }
.recent-cover img { width: 100%; height: 100%; object-fit: cover; }
.recent-cover span { position: absolute; right: 8px; bottom: 8px; padding: 3px 6px; border-radius: 5px; background: rgba(18, 20, 25, .78); color: #fff; font-size: 11px; }
.recent-body { padding: 14px; }
.recent-body h3 { display: -webkit-box; min-height: 42px; margin: 0; overflow: hidden; font-size: 14px; line-height: 1.5; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.recent-body p { margin: 8px 0; color: var(--text-secondary); }
.recent-body small { display: flex; align-items: center; gap: 5px; color: var(--text-tertiary); }
.capability-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
.capability-grid article { padding: 25px; border: 1px solid var(--line-soft); border-radius: var(--radius-lg); background: var(--surface); }
.capability-grid article > span { display: grid; place-items: center; width: 42px; height: 42px; border-radius: 12px; background: var(--brand-soft); color: var(--brand); }
.capability-grid article svg { width: 21px; }
.capability-grid h3 { margin: 17px 0 8px; font-size: 17px; }
.capability-grid p { margin: 0; color: var(--text-secondary); line-height: 1.7; }
.support-note { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 18px; margin: 42px 0 20px; padding: 24px; }
.support-icon { display: grid; place-items: center; width: 48px; height: 48px; border-radius: 14px; background: #fff2e9; color: var(--accent); }
.support-icon svg { width: 23px; }
.support-note h2 { margin: 0 0 6px; font-size: 16px; }
.support-note p { margin: 0; color: var(--text-secondary); line-height: 1.65; }
.support-note a { display: flex; align-items: center; gap: 5px; font-weight: 700; text-decoration: none; white-space: nowrap; }

@media (max-width: 1199px) {
  .hero { grid-template-columns: 1fr; min-height: 0; padding-top: 20px; }
  .hero-copy { max-width: 760px; }
  .hero-copy h1 { font-size: 52px; }
  .parse-panel { max-width: 800px; }
  .recent-grid { grid-template-columns: repeat(3, 1fr); }
}
@media (min-width: 1200px) and (max-width: 1365px) {
  .hero-copy h1 { font-size: 46px; }
}
@media (max-width: 767px) {
  .hero {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: stretch;
    min-height: calc(100dvh - 86px);
    padding: 3px 0 calc(88px + env(safe-area-inset-bottom));
  }
  .parse-panel { order: -1; }
  .hero-copy {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }
  .parse-panel { padding: 18px; border-radius: 17px; }
  .panel-heading { align-items: center; margin-bottom: 18px; }
  .panel-heading h2 { font-size: 18px; }
  .input-row { display: grid; grid-template-columns: 1fr auto; }
  .input-row .el-input { grid-column: 1 / -1; }
  .paste-button { min-width: 0; }
  .clear-button { display: inline-flex; }
  .mode-options { grid-template-columns: 1fr; }
  .mode-options button { min-height: 70px; align-items: center; }
  .mode-options strong { margin-bottom: 2px; }
  .recent-section, .capabilities { padding: 30px 0; }
  .recent-grid { grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .recent-body { padding: 11px; }
  .capability-grid { grid-template-columns: 1fr; }
  .capability-grid article { padding: 20px; }
  .support-note { grid-template-columns: auto 1fr; align-items: start; padding: 18px; }
  .support-note a { grid-column: 2; }
}
@media (max-width: 389px) {
  .panel-heading :deep(.auth-badge) { font-size: 11px; }
  .recent-grid { grid-template-columns: 1fr; }
  .recent-cover { aspect-ratio: 16 / 8; }
}
</style>
