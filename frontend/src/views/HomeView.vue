<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ArrowRight,
  CircleClose,
  DataAnalysis,
  DocumentCopy,
  Film,
  Lock,
  View,
} from '@element-plus/icons-vue'
import AuthStatusBadge from '@/components/AuthStatusBadge.vue'
import MediaDecompositionStage from '@/components/home/MediaDecompositionStage.vue'
import RequestError from '@/components/RequestError.vue'
import { useAuthStore } from '@/stores/auth'
import { useVideosStore } from '@/stores/videos'
import type { AccessMode } from '@/types/api'
import { normalizeVideoInput, VideoInputError } from '@/utils/videoUrl'

const router = useRouter()
const auth = useAuthStore()
const videos = useVideosStore()
const input = ref('')
const accessMode = ref<AccessMode>('auto')
const validationMessage = ref('')

const modes: Array<{ value: AccessMode; title: string; description: string }> = [
  { value: 'auto', title: '智能', description: '优先匿名，按需补充权益' },
  { value: 'anonymous', title: '匿名', description: '不读取已保存 Cookie' },
  { value: 'authenticated', title: '登录态', description: '读取当前账号可用规格' },
]

const canUseAuthenticated = computed(() => auth.isAuthenticated)
const previewVideo = computed(() => videos.recent[0] ?? null)
const sourceReady = computed(() => input.value.trim().length > 0)

onMounted(() => {
  void videos.loadRecent(1)
})

async function pasteFromClipboard(): Promise<void> {
  try {
    input.value = await navigator.clipboard.readText()
    validationMessage.value = ''
  } catch {
    ElMessage.warning('浏览器未授予剪贴板权限，请手动粘贴链接')
  }
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
</script>

<template>
  <div class="home-view">
    <section class="home-hero">
      <div class="hero-content">
        <header class="hero-copy">
          <div class="brand-signature">
            <span class="brand-index">BI / 01</span>
            <span class="brand-line" aria-hidden="true" />
            <span>BILI INSIGHT</span>
          </div>
          <h1>视频，从来不只是一条播放轨。</h1>
          <p class="lead">把画面、声音、字幕与元数据逐层展开，先看清，再预览、分析和下载。</p>
        </header>

        <div class="mobile-stage">
          <MediaDecompositionStage compact :video="previewVideo" :parsing="videos.loading" :source-ready="sourceReady" />
        </div>

        <div class="parse-panel">
          <div class="parse-meta">
            <div class="parse-label">
              <span class="parse-sequence">01</span>
              <span><strong>输入视频源</strong><small>URL / BV / AV / SS / EP</small></span>
            </div>
            <AuthStatusBadge :status="auth.status" :loading="auth.loading" />
          </div>

          <form novalidate @submit.prevent="parseVideo">
            <h2 class="sr-only">粘贴 Bilibili 视频链接</h2>
            <label class="sr-only" for="video-url">Bilibili 视频链接、BV/AV 号或 ss/ep 标识</label>
            <div class="source-input" :class="{ invalid: validationMessage, ready: sourceReady }">
              <span class="source-icon"><el-icon><Film /></el-icon></span>
              <el-input
                id="video-url"
                v-model="input"
                data-testid="video-url-input"
                size="large"
                clearable
                placeholder="粘贴 Bilibili 链接或输入 BV / AV / ss / ep"
                autocomplete="off"
                @input="validationMessage = ''"
                @keyup.enter="parseVideo"
              />
              <el-tooltip content="粘贴链接" placement="top">
                <button class="paste-button" type="button" aria-label="粘贴链接" @click="pasteFromClipboard">
                  <el-icon><DocumentCopy /></el-icon>
                </button>
              </el-tooltip>
            </div>
            <p v-if="validationMessage" class="validation" role="alert"><el-icon><CircleClose /></el-icon>{{ validationMessage }}</p>

            <fieldset class="mode-fieldset">
              <legend>解析身份</legend>
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
                  <span class="mode-dot" aria-hidden="true" />
                  <span><strong>{{ mode.title }}</strong><small>{{ mode.description }}</small></span>
                </button>
              </div>
            </fieldset>

            <div v-if="auth.isAuthenticated && accessMode !== 'authenticated'" class="auth-explanation">
              <el-icon><Lock /></el-icon>
              <span>登录态已加密保存；智能模式仍优先匿名访问。</span>
            </div>

            <el-button class="parse-button" native-type="submit" type="primary" size="large" :loading="videos.loading" data-testid="parse-submit">
              <span>{{ videos.loading ? '正在逐层解析' : '建立视频解析图谱' }}</span>
              <el-icon v-if="!videos.loading"><ArrowRight /></el-icon>
            </el-button>
          </form>

          <RequestError v-if="videos.error" class="parse-error" :error="videos.error" @retry="parseVideo" />
        </div>
      </div>

      <div class="desktop-stage">
        <MediaDecompositionStage :video="previewVideo" :parsing="videos.loading" :source-ready="sourceReady" />
      </div>

      <footer class="workflow-copy">
        <div class="workflow-heading">
          <span>WORKFLOW</span>
          <strong>解析之后，所有步骤都可追踪。</strong>
        </div>
        <div class="workflow-steps">
          <span><el-icon><View /></el-icon>在线预览</span>
          <i aria-hidden="true" />
          <span><el-icon><DataAnalysis /></el-icon>AI 内容分析</span>
          <i aria-hidden="true" />
          <span><el-icon><Film /></el-icon>按规格下载</span>
        </div>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.home-view {
  display: grid;
  width: 100%;
  min-height: calc(100dvh - 72px);
  align-items: center;
  overflow: hidden;
}
.home-hero {
  display: grid;
  grid-template-columns: minmax(420px, .92fr) minmax(470px, 1.08fr);
  width: 100%;
  gap: 22px clamp(28px, 3.4vw, 56px);
  align-items: center;
}
.hero-content { min-width: 0; }
.hero-copy { max-width: 610px; }
.brand-signature { display: flex; align-items: center; gap: 10px; color: var(--brand); font-family: "SFMono-Regular", Consolas, monospace; font-size: 10px; font-weight: 800; }
.brand-index { color: var(--accent); }
.brand-line { width: 38px; height: 1px; background: var(--line); }
.hero-copy h1 { max-width: 620px; margin: 20px 0 15px; font-size: 54px; line-height: 1.04; letter-spacing: 0; }
.lead { max-width: 570px; margin: 0; color: var(--text-secondary); font-size: 15px; line-height: 1.7; }
.parse-panel { margin-top: 29px; }
.parse-meta { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 10px; }
.parse-label { display: flex; align-items: center; gap: 9px; }
.parse-sequence { display: grid; place-items: center; width: 26px; height: 26px; border: 1px solid var(--brand); color: var(--brand); font-family: "SFMono-Regular", Consolas, monospace; font-size: 9px; }
.parse-label strong, .parse-label small { display: block; }.parse-label strong { font-size: 12px; }.parse-label small { margin-top: 2px; color: var(--text-tertiary); font-family: "SFMono-Regular", Consolas, monospace; font-size: 7px; }
.source-input { display: grid; grid-template-columns: 46px minmax(0, 1fr) 46px; align-items: center; min-height: 58px; overflow: hidden; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); box-shadow: 0 12px 32px rgba(31, 36, 51, .07); transition: border-color .18s, box-shadow .18s; }
.source-input:focus-within, .source-input.ready { border-color: var(--brand); box-shadow: 0 12px 32px rgba(12, 127, 121, .1); }
.source-input.invalid { border-color: var(--danger); }
.source-icon { display: grid; place-items: center; height: 30px; border-right: 1px solid var(--line-soft); color: var(--brand); font-size: 17px; }
.source-input :deep(.el-input__wrapper) { min-height: 56px; padding-inline: 14px; background: transparent; box-shadow: none; }
.source-input :deep(.el-input__inner) { font-size: 13px; }
.paste-button { display: grid; place-items: center; width: 36px; height: 36px; padding: 0; border: 0; border-radius: 6px; background: var(--surface-muted); color: var(--text-secondary); cursor: pointer; }
.paste-button:hover { background: var(--brand-soft); color: var(--brand); }
.validation { display: flex; align-items: center; gap: 7px; margin: 8px 0 0; color: var(--danger); font-size: 12px; line-height: 1.4; }
.mode-fieldset { min-width: 0; padding: 0; margin: 14px 0 0; border: 0; }
.mode-fieldset legend { margin-bottom: 7px; color: var(--text-tertiary); font-size: 10px; font-weight: 700; }
.mode-options { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; padding: 5px; border: 1px solid var(--line-soft); border-radius: 8px; background: var(--surface-muted); }
.mode-options button { display: flex; align-items: flex-start; gap: 7px; min-width: 0; min-height: 54px; padding: 8px 9px; border: 1px solid transparent; border-radius: 6px; background: transparent; color: var(--text-primary); text-align: left; cursor: pointer; }
.mode-options button.selected { border-color: var(--line); background: var(--surface); box-shadow: 0 4px 12px rgba(31, 36, 51, .06); }
.mode-options button.disabled:not(.selected) { opacity: .55; }
.mode-dot { flex: 0 0 auto; width: 7px; height: 7px; margin-top: 4px; border: 1px solid var(--text-tertiary); border-radius: 50%; }
.selected .mode-dot { border-color: var(--brand); background: var(--brand); box-shadow: 0 0 0 3px var(--brand-soft); }
.mode-options strong, .mode-options small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mode-options strong { font-size: 11px; }.mode-options small { margin-top: 3px; color: var(--text-tertiary); font-size: 8px; }
.auth-explanation { display: flex; align-items: center; gap: 6px; margin-top: 9px; color: var(--text-tertiary); font-size: 9px; }
.parse-button { width: 100%; min-height: 52px; margin-top: 12px; border-radius: 7px; }
.parse-button :deep(span) { display: flex; align-items: center; justify-content: center; gap: 10px; }
.parse-error { margin-top: 12px; }
.mobile-stage { display: none; }
.desktop-stage { min-width: 0; }
.workflow-copy { display: flex; grid-column: 1 / -1; align-items: center; justify-content: space-between; gap: 24px; min-height: 56px; padding-top: 18px; border-top: 1px solid var(--line); }
.workflow-heading { display: flex; align-items: center; gap: 12px; min-width: 0; }
.workflow-heading span { color: var(--accent); font-family: "SFMono-Regular", Consolas, monospace; font-size: 8px; }
.workflow-heading strong { font-size: 11px; }
.workflow-steps { display: flex; align-items: center; justify-content: flex-end; gap: 10px; color: var(--text-secondary); font-size: 10px; }
.workflow-steps span { display: flex; align-items: center; gap: 5px; white-space: nowrap; }.workflow-steps .el-icon { color: var(--brand); font-size: 13px; }.workflow-steps > i { width: 18px; height: 1px; background: var(--line); }

@media (max-width: 1365px) {
  .home-view { min-height: calc(100dvh - 60px); }
  .home-hero { grid-template-columns: minmax(390px, .9fr) minmax(430px, 1.1fr); gap: 18px 30px; }
  .hero-copy h1 { margin-top: 16px; font-size: 45px; }
  .parse-panel { margin-top: 21px; }
}

@media (min-width: 768px) and (max-width: 1040px) {
  .home-hero { grid-template-columns: 1fr; }
  .desktop-stage { display: none; }
  .mobile-stage { display: block; margin-top: 18px; }
  .hero-copy { max-width: 700px; }.hero-copy h1 { max-width: 700px; }
}

@media (max-width: 767px) {
  .home-view { min-height: calc(100dvh - 174px); overflow: visible; }
  .home-hero { display: flex; flex-direction: column; gap: 8px; align-items: stretch; }
  .brand-signature { gap: 7px; font-size: 8px; }.brand-line { width: 24px; }
  .hero-copy h1 { margin: 9px 0 6px; font-size: 29px; line-height: 1.08; }
  .lead { font-size: 10px; line-height: 1.45; }
  .desktop-stage { display: none; }
  .mobile-stage { display: block; margin-top: 2px; }
  .parse-panel { margin-top: 1px; }
  .parse-meta { margin-bottom: 5px; }.parse-meta :deep(.auth-badge) { font-size: 9px; }
  .parse-sequence { width: 21px; height: 21px; font-size: 7px; }.parse-label { gap: 6px; }.parse-label strong { font-size: 10px; }.parse-label small { display: none; }
  .source-input { grid-template-columns: 38px minmax(0, 1fr) 42px; min-height: 46px; }
  .source-input :deep(.el-input__wrapper) { min-height: 44px; padding-inline: 9px; }.source-input :deep(.el-input__inner) { font-size: 11px; }
  .source-icon { height: 24px; font-size: 14px; }.paste-button { width: 34px; height: 34px; }
  .mode-fieldset { margin-top: 7px; }.mode-fieldset legend { margin-bottom: 4px; font-size: 8px; }
  .mode-options { gap: 3px; padding: 3px; }
  .mode-options button { align-items: center; justify-content: center; min-height: 44px; padding: 5px 4px; text-align: center; }
  .mode-options button > span:last-child { min-width: 0; }.mode-options strong { font-size: 10px; }.mode-options small, .mode-dot { display: none; }
  .auth-explanation { display: none; }
  .parse-button { min-height: 46px; margin-top: 7px; }
  .workflow-copy { min-height: 33px; padding-top: 7px; }
  .workflow-heading { gap: 7px; }.workflow-heading span { font-size: 6px; }.workflow-heading strong { font-size: 9px; }
  .workflow-steps { display: none; }
}

@media (max-width: 374px) {
  .hero-copy h1 { font-size: 26px; }
  .lead { font-size: 9px; }
  .mobile-stage :deep(.decomposition-stage) { height: 102px; }
  .mobile-stage :deep(.stage-canvas) { height: 71px; }
}
</style>
