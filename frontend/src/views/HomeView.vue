<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowRight, CircleClose, DocumentCopy, Film, Lock } from '@element-plus/icons-vue'
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
  { value: 'auto', title: '智能', description: '优先匿名，必要时使用登录权益' },
  { value: 'anonymous', title: '匿名', description: '不读取已保存 Cookie' },
  { value: 'authenticated', title: '登录态', description: '读取账号可访问规格' },
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
    <section class="landing-page">
      <div class="hero-showcase">
        <header class="hero-copy">
          <div class="hero-meta">
            <span class="brand-signature"><b>BI / 01</b><i aria-hidden="true" /><strong>BILI INSIGHT</strong></span>
            <AuthStatusBadge :status="auth.status" :loading="auth.loading" />
          </div>
          <span class="hero-kicker">VIDEO INTELLIGENCE WORKSPACE</span>
          <h1>把视频拆开看。</h1>
          <p>画面、声音、字幕和元数据，不再藏在一个播放按钮后面。</p>
          <div class="hero-capabilities" aria-label="产品能力">
            <span>真实规格</span><span>在线预览</span><span>AI 分析</span><span>可追踪产物</span>
          </div>
        </header>

        <div class="visual-stage">
          <MediaDecompositionStage :video="previewVideo" :parsing="videos.loading" :source-ready="sourceReady" />
        </div>
      </div>

      <div class="parse-panel surface-card">
        <div class="panel-heading">
          <div><span>01 / PARSE</span><strong>开始一次解析</strong></div>
          <div v-if="auth.isAuthenticated && accessMode !== 'authenticated'" class="auth-explanation"><el-icon><Lock /></el-icon><span>智能模式优先匿名访问</span></div>
        </div>

        <form novalidate @submit.prevent="parseVideo">
          <h2 class="sr-only">粘贴 Bilibili 视频链接</h2>
          <label class="sr-only" for="video-url">Bilibili 视频链接、BV/AV 号或 ss/ep 标识</label>
          <div class="parse-controls">
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
                <button class="paste-button" type="button" aria-label="粘贴链接" @click="pasteFromClipboard"><el-icon><DocumentCopy /></el-icon></button>
              </el-tooltip>
            </div>

            <fieldset class="mode-fieldset">
              <legend class="sr-only">解析身份</legend>
              <button
                v-for="mode in modes"
                :key="mode.value"
                type="button"
                :data-testid="`access-mode-${mode.value}`"
                :class="{ selected: accessMode === mode.value, disabled: mode.value === 'authenticated' && !canUseAuthenticated }"
                :aria-pressed="accessMode === mode.value"
                :title="mode.description"
                @click="selectMode(mode.value)"
              >
                <span class="mode-node" aria-hidden="true" /><strong>{{ mode.title }}</strong>
              </button>
            </fieldset>

            <el-button class="parse-button" native-type="submit" type="primary" size="large" :loading="videos.loading" data-testid="parse-submit">
              <span>{{ videos.loading ? '正在展开结构' : '开始解析' }}</span><el-icon v-if="!videos.loading"><ArrowRight /></el-icon>
            </el-button>
          </div>
          <p v-if="validationMessage" class="validation" role="alert"><el-icon><CircleClose /></el-icon>{{ validationMessage }}</p>
        </form>
        <RequestError v-if="videos.error" class="parse-error" :error="videos.error" @retry="parseVideo" />
      </div>

      <footer class="workflow-copy">
        <span>解析视频源</span><i /><span>选择实际规格</span><i /><span>预览或创建任务</span>
        <small>所有耗时操作、失败原因和产物都进入可追踪工作流。</small>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.home-view { display: grid; width: 100%; min-height: calc(100dvh - 72px); align-items: center; }
.landing-page { display: grid; width: 100%; gap: 14px; }
.hero-showcase { display: grid; grid-template-columns: minmax(390px, .8fr) minmax(500px, 1.2fr); gap: clamp(24px, 3vw, 48px); align-items: center; min-height: 430px; }
.hero-copy { min-width: 0; }
.hero-meta { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 24px; }
.brand-signature { display: flex; align-items: center; gap: 9px; color: var(--brand); font-family: "SFMono-Regular", Consolas, monospace; font-size: 8px; }.brand-signature b { color: var(--accent); }.brand-signature i { width: 32px; height: 1px; background: var(--line); }.brand-signature strong { font-size: 8px; }
.hero-kicker { color: var(--text-tertiary); font-family: "SFMono-Regular", Consolas, monospace; font-size: 8px; }
.hero-copy h1 { margin: 14px 0 14px; font-size: 58px; line-height: 1.02; letter-spacing: 0; }
.hero-copy p { max-width: 470px; margin: 0; color: var(--text-secondary); font-size: 15px; line-height: 1.7; }
.hero-capabilities { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 24px; }.hero-capabilities span { padding: 5px 8px; border: 1px solid var(--line); border-radius: 4px; background: var(--surface); color: var(--text-secondary); font-size: 9px; }
.visual-stage { min-width: 0; height: 430px; }
.parse-panel { padding: 14px; border-radius: 8px; }
.panel-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 10px; }.panel-heading > div:first-child { display: flex; align-items: center; gap: 10px; }.panel-heading span { color: var(--text-tertiary); font-family: "SFMono-Regular", Consolas, monospace; font-size: 8px; }.panel-heading strong { font-size: 12px; }.auth-explanation { display: flex; align-items: center; gap: 5px; color: var(--text-tertiary); font-size: 9px; }
.parse-controls { display: grid; grid-template-columns: minmax(300px, 1fr) minmax(260px, .62fr) minmax(160px, .28fr); gap: 8px; }
.source-input { display: grid; grid-template-columns: 44px minmax(0, 1fr) 44px; align-items: center; min-width: 0; height: 50px; overflow: hidden; border: 1px solid var(--line); border-radius: 6px; background: var(--surface); transition: border-color .18s, box-shadow .18s; }.source-input:focus-within, .source-input.ready { border-color: var(--brand); box-shadow: 0 0 0 2px rgba(12, 127, 121, .09); }.source-input.invalid { border-color: var(--danger); }
.source-icon { display: grid; place-items: center; height: 28px; border-right: 1px solid var(--line-soft); color: var(--brand); font-size: 17px; }.source-input :deep(.el-input__wrapper) { min-height: 48px; padding-inline: 12px; background: transparent; box-shadow: none; }.source-input :deep(.el-input__inner) { font-size: 12px; }
.paste-button { display: grid; place-items: center; width: 36px; height: 36px; padding: 0; border: 0; border-radius: 5px; background: var(--surface-muted); color: var(--text-secondary); cursor: pointer; }.paste-button:hover { background: var(--brand-soft); color: var(--brand); }
.mode-fieldset { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); min-width: 0; padding: 3px; margin: 0; border: 1px solid var(--line); border-radius: 6px; background: var(--surface-muted); }.mode-fieldset button { display: flex; align-items: center; justify-content: center; gap: 6px; min-width: 0; min-height: 44px; padding: 4px 5px; border: 1px solid transparent; border-radius: 4px; background: transparent; color: var(--text-secondary); cursor: pointer; }.mode-fieldset button.selected { border-color: var(--line); background: var(--surface); color: var(--text-primary); box-shadow: 0 3px 9px rgba(31, 36, 51, .05); }.mode-fieldset button.disabled:not(.selected) { opacity: .5; }
.mode-node { width: 6px; height: 6px; border: 1px solid var(--text-tertiary); border-radius: 50%; }.selected .mode-node { border-color: var(--brand); background: var(--brand); box-shadow: 0 0 0 3px var(--brand-soft); }.mode-fieldset strong { overflow: hidden; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.parse-button { width: 100%; height: 50px; min-height: 50px; margin: 0; border-radius: 6px; }.parse-button :deep(span) { display: flex; align-items: center; justify-content: center; gap: 8px; }
.validation { display: flex; align-items: center; gap: 6px; margin: 7px 0 0; color: var(--danger); font-size: 10px; }.parse-error { margin-top: 9px; }
.workflow-copy { display: flex; align-items: center; gap: 9px; min-height: 28px; padding-inline: 3px; color: var(--text-tertiary); font-size: 9px; }.workflow-copy > i { width: 20px; height: 1px; background: var(--line); }.workflow-copy > span:first-child { color: var(--brand); }.workflow-copy small { margin-left: auto; color: var(--text-tertiary); font-size: 9px; }

@media (max-width: 1365px) {
  .home-view { min-height: calc(100dvh - 60px); }
  .hero-showcase { grid-template-columns: minmax(350px, .75fr) minmax(440px, 1.25fr); min-height: 400px; }
  .hero-copy h1 { font-size: 50px; }.visual-stage { height: 400px; }
}

@media (min-width: 768px) and (max-width: 1040px) {
  .hero-showcase { grid-template-columns: 1fr; gap: 13px; min-height: 0; }.hero-meta { margin-bottom: 12px; }.hero-copy h1 { margin-block: 8px; font-size: 42px; }.hero-copy p { font-size: 12px; }.hero-capabilities { margin-top: 12px; }.visual-stage { height: 300px; }
  .parse-controls { grid-template-columns: minmax(260px, 1fr) minmax(250px, .8fr); }.parse-button { grid-column: 1 / -1; }
}

@media (max-width: 767px) {
  .home-view { min-height: calc(100dvh - 174px); }
  .landing-page { gap: 7px; }
  .hero-showcase { grid-template-columns: 1fr; gap: 6px; min-height: 0; }
  .hero-meta { margin-bottom: 5px; }.hero-meta :deep(.auth-badge) { display: none; }.brand-signature { font-size: 6px; }.brand-signature i { width: 20px; }.brand-signature strong { font-size: 6px; }
  .hero-kicker { display: none; }.hero-copy h1 { margin: 0 0 4px; font-size: 31px; }.hero-copy p { max-width: 340px; font-size: 10px; line-height: 1.45; }.hero-capabilities { display: none; }
  .visual-stage { height: 210px; }
  .parse-panel { padding: 8px; }.panel-heading { min-height: 20px; margin-bottom: 5px; }.panel-heading > div:first-child { gap: 7px; }.panel-heading strong { font-size: 10px; }.auth-explanation { display: none; }
  .parse-controls { grid-template-columns: 1fr; gap: 5px; }.source-input { grid-template-columns: 38px minmax(0, 1fr) 42px; height: 44px; }.source-input :deep(.el-input__wrapper) { min-height: 42px; padding-inline: 8px; }.source-input :deep(.el-input__inner) { font-size: 10px; }.source-icon { height: 24px; font-size: 14px; }.paste-button { width: 34px; height: 34px; }
  .mode-fieldset { gap: 3px; }.mode-fieldset button { min-height: 44px; }.mode-node { display: none; }.mode-fieldset strong { font-size: 10px; }
  .parse-button { height: 46px; min-height: 46px; }
  .validation { position: absolute; right: 8px; bottom: -19px; left: 8px; }.workflow-copy { justify-content: center; min-height: 22px; font-size: 7px; }.workflow-copy small { display: none; }.workflow-copy > i { width: 12px; }
}

@media (max-width: 374px) {
  .hero-copy h1 { font-size: 28px; }.visual-stage { height: 198px; }
}
</style>
