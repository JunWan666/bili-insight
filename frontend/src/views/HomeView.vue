<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ArrowRight,
  Check,
  CircleClose,
  Delete,
  DocumentCopy,
  Film,
  Lock,
} from '@element-plus/icons-vue'
import AuthStatusBadge from '@/components/AuthStatusBadge.vue'
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
</script>

<template>
  <div class="home-view">
    <section class="workbench">
      <header class="hero-copy">
        <div class="hero-signal hero-signal-left" aria-hidden="true"><i v-for="index in 7" :key="index" /></div>
        <div class="hero-signal hero-signal-right" aria-hidden="true"><i v-for="index in 7" :key="index" /></div>
        <p class="eyebrow">LOCAL MEDIA WORKBENCH</p>
        <h1>从一个链接开始，<br><span>看清视频的每一层。</span></h1>
        <p class="lead">解析实际可访问的音视频规格，按需下载、合并并生成可追踪的内容与媒体分析。</p>
      </header>

      <div class="parse-panel surface-card">
        <div class="panel-trace" aria-hidden="true"><i /><i /><i /><span /></div>
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

          <div class="parse-footer">
            <div v-if="auth.isAuthenticated && accessMode !== 'authenticated'" class="auth-explanation">
              <el-icon><Lock /></el-icon>
              <span>登录态已保存，本次仍优先匿名。</span>
            </div>
            <el-button class="parse-button" native-type="submit" type="primary" size="large" :loading="videos.loading" data-testid="parse-submit">
              {{ videos.loading ? '正在安全解析…' : '开始解析' }}
              <el-icon v-if="!videos.loading"><ArrowRight /></el-icon>
            </el-button>
          </div>
        </form>

        <RequestError v-if="videos.error" class="parse-error" :error="videos.error" @retry="parseVideo" />
      </div>

      <footer class="workflow-copy">
        <span class="step-label">工作流</span>
        <h2>解析之后，你可以继续</h2>
        <p>所有耗时操作进入任务队列，进度、失败原因和产物都可追踪。</p>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.home-view {
  display: grid;
  width: 100%;
  min-height: calc(100dvh - 72px);
  place-items: center;
  overflow: hidden;
}
.workbench {
  display: grid;
  width: min(1120px, 100%);
  gap: 22px;
}
.eyebrow, .step-label { color: var(--brand); font-size: 11px; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; }
.hero-copy { position: relative; text-align: center; }
.hero-signal { position: absolute; top: 30px; display: flex; align-items: center; gap: 5px; width: 150px; height: 42px; padding-inline: 12px; border-top: 1px solid var(--line-soft); border-bottom: 1px solid var(--line-soft); opacity: .72; }
.hero-signal::after { flex: 1; height: 1px; background: var(--line); content: ''; }
.hero-signal i { width: 2px; border-radius: 2px; background: var(--brand); }
.hero-signal i:nth-child(1), .hero-signal i:nth-child(7) { height: 6px; }.hero-signal i:nth-child(2), .hero-signal i:nth-child(6) { height: 13px; }.hero-signal i:nth-child(3), .hero-signal i:nth-child(5) { height: 21px; }.hero-signal i:nth-child(4) { height: 30px; background: var(--accent); }
.hero-signal-left { left: 18px; }.hero-signal-right { right: 18px; transform: scaleX(-1); }
.hero-copy h1 { margin: 10px 0 13px; font-size: 52px; line-height: 1.06; letter-spacing: 0; }
.hero-copy h1 span { color: var(--brand); }
.lead { max-width: 720px; margin: 0 auto; color: var(--text-secondary); font-size: 15px; line-height: 1.6; }
.parse-panel { position: relative; padding: 22px; border-radius: 14px; }
.panel-trace { position: absolute; top: -1px; left: 22px; display: flex; align-items: center; gap: 5px; width: 108px; height: 1px; }
.panel-trace i { width: 5px; height: 5px; border: 1px solid var(--surface); border-radius: 50%; background: var(--brand); }.panel-trace i:nth-child(2) { background: var(--accent); }.panel-trace span { flex: 1; height: 1px; background: var(--brand); }
.panel-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
.panel-heading h2 { margin: 5px 0 0; font-size: 18px; }
.input-row { display: flex; gap: 8px; }
.input-row :deep(.el-input__wrapper) { min-height: 52px; }
.input-row.invalid :deep(.el-input__wrapper) { box-shadow: 0 0 0 1px var(--danger) inset; }
.paste-button { min-width: 92px; }
.clear-button { display: none; }
.validation { display: flex; align-items: center; gap: 7px; margin: 9px 0 0; color: var(--danger); line-height: 1.45; }
.mode-fieldset { min-width: 0; padding: 0; margin: 15px 0 0; border: 0; }
.mode-fieldset legend { margin-bottom: 10px; color: var(--text-secondary); font-size: 13px; font-weight: 700; }
.mode-options { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.mode-options button { display: flex; align-items: flex-start; gap: 9px; min-height: 72px; padding: 11px 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); color: var(--text-primary); text-align: left; cursor: pointer; transition: border-color .16s, background .16s; }
.mode-options button.selected { border-color: var(--brand); background: var(--brand-soft); }
.mode-options button.disabled:not(.selected) { opacity: .62; }
.radio-mark { display: grid; place-items: center; flex: 0 0 auto; width: 19px; height: 19px; margin-top: 1px; border: 1.5px solid var(--line); border-radius: 50%; color: transparent; }
.selected .radio-mark { border-color: var(--brand); background: var(--brand); color: white; }
.radio-mark svg { width: 12px; }
.mode-options strong, .mode-options small { display: block; }
.mode-options strong { margin-bottom: 4px; font-size: 13px; }
.mode-options small { color: var(--text-tertiary); line-height: 1.35; }
.parse-footer { display: grid; justify-items: center; gap: 8px; margin-top: 14px; }
.auth-explanation { display: flex; align-items: center; gap: 7px; padding: 7px 10px; border-radius: 8px; background: var(--surface-muted); color: var(--text-secondary); font-size: 11px; line-height: 1.4; }
.parse-button { width: min(380px, 100%); min-height: 52px; margin: 0; }
.parse-error { margin-top: 14px; }
.workflow-copy { padding: 2px 4px 0; }
.workflow-copy h2 { margin: 5px 0 4px; font-size: 23px; letter-spacing: 0; }
.workflow-copy p { margin: 0; color: var(--text-secondary); line-height: 1.5; }

@media (max-width: 1365px) {
  .home-view { min-height: calc(100dvh - 60px); }
  .workbench { gap: 18px; }
  .hero-copy h1 { font-size: 44px; }
  .parse-panel { padding: 19px; }
  .mode-options button { min-height: 68px; }
  .hero-signal { display: none; }
}

@media (max-width: 767px) {
  .home-view { min-height: calc(100dvh - 174px); overflow: visible; }
  .workbench { gap: 16px; }
  .hero-copy h1 { margin: 7px 0 8px; font-size: 32px; line-height: 1.08; }
  .lead { font-size: 12px; line-height: 1.45; }
  .parse-panel { padding: 15px; border-radius: 14px; }
  .panel-trace { left: 15px; }
  .panel-heading { margin-bottom: 12px; }
  .panel-heading h2 { font-size: 16px; }
  .panel-heading :deep(.auth-badge) { font-size: 10px; }
  .input-row { display: grid; grid-template-columns: 1fr auto; }
  .input-row .el-input { grid-column: 1 / -1; }
  .paste-button { min-width: 0; }
  .clear-button { display: inline-flex; }
  .mode-fieldset { margin-top: 12px; }
  .mode-fieldset legend { margin-bottom: 7px; font-size: 12px; }
  .mode-options { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; }
  .mode-options button { justify-content: center; align-items: center; min-height: 54px; padding: 7px 5px; text-align: center; }
  .mode-options button > span:last-child { min-width: 0; }
  .mode-options strong { margin: 0; font-size: 12px; line-height: 1.2; }
  .mode-options small { display: none; }
  .radio-mark { width: 17px; height: 17px; }
  .parse-footer { margin-top: 11px; }
  .auth-explanation { padding-block: 6px; }
  .parse-button { min-height: 48px; }
  .workflow-copy h2 { font-size: 20px; }
  .workflow-copy p { font-size: 12px; }
}

@media (max-width: 374px) {
  .hero-copy h1 { font-size: 29px; }
  .parse-panel { padding-inline: 13px; }
  .mode-options button { gap: 5px; }
}
</style>
