<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ArrowRight,
  CircleClose,
  Clock,
  DocumentCopy,
  Film,
  User,
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
  { value: 'auto', title: '智能', description: '优先匿名，需要时再补充登录画质' },
  { value: 'anonymous', title: '匿名', description: '本次解析不读取已保存 Cookie' },
  { value: 'authenticated', title: '登录态', description: '使用账号当前可访问的媒体规格' },
]

const canUseAuthenticated = computed(() => auth.isAuthenticated)
const previewVideo = computed(() => videos.recent[0] ?? null)
const sourceReady = computed(() => input.value.trim().length > 0)
const selectedMode = computed(() => modes.find((mode) => mode.value === accessMode.value) ?? modes[0])

onMounted(() => {
  void videos.loadRecent(1)
})

function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const remain = total % 60
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remain).padStart(2, '0')}`
    : `${minutes}:${String(remain).padStart(2, '0')}`
}

function formatParsedAt(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '最近解析'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

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
      <header class="hero-intro">
        <h1><span>从一条链接开始，</span><strong>看清视频的每一层。</strong></h1>
        <p class="hero-description">
          <span class="layer-word layer-picture">画面</span>、<span class="layer-word layer-sound">声音</span>、<span class="layer-word layer-caption">字幕</span>、<span class="layer-word layer-data">规格</span>与内容线索，逐层展开。先播放，再决定是否下载。
        </p>
      </header>

      <section class="parse-workbench surface-card" aria-labelledby="parse-heading">
        <div class="workbench-heading">
          <div>
            <span>START HERE</span>
            <h2 id="parse-heading">把视频链接放进来</h2>
          </div>
          <div class="auth-context">
            <span>当前账号</span>
            <AuthStatusBadge :status="auth.status" :loading="auth.loading" compact />
          </div>
        </div>

        <form novalidate @submit.prevent="parseVideo">
          <label class="sr-only" for="video-url">Bilibili 视频链接、BV/AV 号或 ss/ep 标识</label>
          <div class="parse-row">
            <div class="source-input" :class="{ invalid: validationMessage, ready: sourceReady }">
              <span class="source-icon"><el-icon><Film /></el-icon></span>
              <el-input
                id="video-url"
                v-model="input"
                data-testid="video-url-input"
                size="large"
                clearable
                placeholder="粘贴 Bilibili 链接，或输入 BV / AV / ss / ep"
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

            <el-button class="parse-button" native-type="submit" type="primary" size="large" :loading="videos.loading" data-testid="parse-submit">
              <span>{{ videos.loading ? '正在解析' : '展开这个视频' }}</span><el-icon v-if="!videos.loading"><ArrowRight /></el-icon>
            </el-button>
          </div>

          <div class="parse-options">
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
            <p><strong>{{ selectedMode.title }}模式</strong><span>{{ selectedMode.description }}</span></p>
            <span class="support-note">支持普通视频与番剧 · BV / AV / ss / ep</span>
          </div>

          <p v-if="validationMessage" class="validation" role="alert"><el-icon><CircleClose /></el-icon>{{ validationMessage }}</p>
        </form>
        <RequestError v-if="videos.error" class="parse-error" :error="videos.error" @retry="parseVideo" />
      </section>

      <section v-if="previewVideo" class="recent-result surface-card" data-testid="recent-result-preview" aria-labelledby="recent-result-heading">
        <RouterLink :to="`/videos/${previewVideo.id}`" class="recent-cover" :aria-label="`查看 ${previewVideo.title}`">
          <img :src="previewVideo.coverUrl" :alt="`${previewVideo.title} 封面`" referrerpolicy="no-referrer">
          <span><el-icon><Clock /></el-icon>{{ formatDuration(previewVideo.duration) }}</span>
        </RouterLink>

        <div class="recent-copy">
          <span class="recent-eyebrow">RECENTLY UNFOLDED / 最近一次解析</span>
          <h2 id="recent-result-heading">{{ previewVideo.title }}</h2>
          <div class="recent-meta">
            <span><el-icon><User /></el-icon>{{ previewVideo.ownerName }}</span>
            <span>{{ previewVideo.bvid }}</span>
            <span>{{ formatParsedAt(previewVideo.parsedAt) }}</span>
          </div>
        </div>

        <RouterLink :to="`/videos/${previewVideo.id}`" class="continue-link">
          <span>继续查看</span><el-icon><ArrowRight /></el-icon>
        </RouterLink>
      </section>

      <footer class="workflow-line" aria-label="解析工作流">
        <span><b>01</b>识别视频</span><i aria-hidden="true" /><span><b>02</b>核对规格</span><i aria-hidden="true" /><span><b>03</b>预览或下载</span>
        <small>进度、失败原因与产物统一进入任务队列。</small>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.home-view { display: grid; width: 100%; min-height: calc(100dvh - 72px); align-items: center; }
.landing-page { display: grid; width: 100%; gap: 18px; }
.hero-intro { display: grid; justify-items: center; text-align: center; }
.hero-intro h1 { margin: 0; font-size: 68px; font-weight: 850; line-height: 1.08; letter-spacing: 0; }
.hero-intro h1 span { display: block; margin-bottom: 5px; color: var(--text-primary); font-size: .72em; font-weight: 760; }
.hero-intro h1 strong { display: block; color: var(--accent); font-weight: inherit; animation: headline-color 7s ease-in-out infinite; }
.hero-description { max-width: 760px; margin: 17px 0 0; color: var(--text-secondary); font-size: 16px; line-height: 1.8; }
.layer-word { font-weight: 800; transition: color .2s ease; }.layer-picture, .layer-data { color: var(--brand); }.layer-sound, .layer-caption { color: var(--accent); }

.parse-workbench { position: relative; width: min(1060px, 100%); margin-inline: auto; padding: 18px; border-color: color-mix(in srgb, var(--brand) 28%, var(--line-soft)); border-radius: 8px; box-shadow: 0 18px 48px rgba(31, 36, 51, .09); }
.parse-workbench::before { position: absolute; top: -1px; left: 28px; width: 88px; height: 3px; background: var(--brand); content: ""; }
.workbench-heading { display: flex; align-items: end; justify-content: space-between; gap: 16px; margin-bottom: 13px; }
.workbench-heading > div:first-child { display: flex; align-items: baseline; gap: 10px; }.workbench-heading > div:first-child > span { color: var(--brand); font-family: "SFMono-Regular", Consolas, monospace; font-size: 8px; font-weight: 750; }.workbench-heading h2 { margin: 0; font-size: 14px; letter-spacing: 0; }.auth-context { display: flex; align-items: center; gap: 8px; color: var(--text-tertiary); font-size: 9px; }
.parse-row { display: grid; grid-template-columns: minmax(0, 1fr) 210px; gap: 9px; }
.source-input { display: grid; grid-template-columns: 48px minmax(0, 1fr) 48px; align-items: center; min-width: 0; height: 58px; overflow: hidden; border: 1px solid var(--line); border-radius: 6px; background: var(--surface); transition: border-color .18s ease, box-shadow .18s ease; }
.source-input:focus-within, .source-input.ready { border-color: var(--brand); box-shadow: 0 0 0 3px rgba(12, 127, 121, .09); }.source-input.invalid { border-color: var(--danger); }
.source-icon { display: grid; place-items: center; height: 32px; border-right: 1px solid var(--line-soft); color: var(--brand); font-size: 19px; }.source-input :deep(.el-input__wrapper) { min-height: 56px; padding-inline: 14px; background: transparent; box-shadow: none; }.source-input :deep(.el-input__inner) { font-size: 13px; }
.paste-button { display: grid; place-items: center; width: 38px; height: 38px; padding: 0; border: 0; border-radius: 5px; background: var(--surface-muted); color: var(--text-secondary); cursor: pointer; }.paste-button:hover { background: var(--brand-soft); color: var(--brand); }
.parse-button { width: 100%; height: 58px; min-height: 58px; border-radius: 6px; }.parse-button :deep(span) { display: flex; align-items: center; justify-content: center; gap: 9px; }
.parse-options { display: grid; grid-template-columns: 274px minmax(0, 1fr) auto; align-items: center; gap: 14px; margin-top: 10px; }
.mode-fieldset { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); min-width: 0; padding: 3px; margin: 0; border: 1px solid var(--line); border-radius: 6px; background: var(--surface-muted); }.mode-fieldset button { display: flex; align-items: center; justify-content: center; gap: 6px; min-width: 0; min-height: 44px; padding: 4px 7px; border: 1px solid transparent; border-radius: 4px; background: transparent; color: var(--text-secondary); cursor: pointer; }.mode-fieldset button.selected { border-color: var(--line); background: var(--surface); color: var(--text-primary); box-shadow: 0 3px 9px rgba(31, 36, 51, .05); }.mode-fieldset button.disabled:not(.selected) { opacity: .5; }
.mode-node { width: 6px; height: 6px; border: 1px solid var(--text-tertiary); border-radius: 50%; }.selected .mode-node { border-color: var(--brand); background: var(--brand); box-shadow: 0 0 0 3px var(--brand-soft); }.mode-fieldset strong { overflow: hidden; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.parse-options > p { display: flex; min-width: 0; gap: 8px; margin: 0; overflow: hidden; font-size: 10px; white-space: nowrap; }.parse-options > p strong { color: var(--text-primary); }.parse-options > p span { overflow: hidden; color: var(--text-tertiary); text-overflow: ellipsis; }.support-note { color: var(--text-tertiary); font-size: 9px; white-space: nowrap; }
.validation { display: flex; align-items: center; gap: 6px; margin: 8px 0 0; color: var(--danger); font-size: 10px; }.parse-error { margin-top: 10px; }

.recent-result { display: grid; grid-template-columns: 184px minmax(0, 1fr) auto; align-items: center; width: min(1060px, 100%); min-height: 118px; margin-inline: auto; overflow: hidden; border-radius: 8px; box-shadow: none; }
.recent-cover { position: relative; align-self: stretch; min-height: 116px; overflow: hidden; background: var(--surface-muted); }.recent-cover img { width: 100%; height: 100%; object-fit: cover; transition: transform .2s ease; }.recent-cover:hover img { transform: scale(1.025); }.recent-cover > span { position: absolute; right: 8px; bottom: 8px; display: flex; align-items: center; gap: 4px; padding: 3px 6px; border-radius: 4px; background: rgba(20, 23, 31, .82); color: #fff; font-size: 9px; }
.recent-copy { min-width: 0; padding: 16px 20px; }.recent-eyebrow { color: var(--brand); font-family: "SFMono-Regular", Consolas, monospace; font-size: 8px; font-weight: 750; }.recent-copy h2 { margin: 7px 0 8px; overflow: hidden; font-size: 16px; line-height: 1.35; text-overflow: ellipsis; white-space: nowrap; }.recent-meta { display: flex; align-items: center; gap: 14px; min-width: 0; color: var(--text-tertiary); font-size: 10px; }.recent-meta span { display: flex; align-items: center; gap: 4px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.continue-link { display: flex; align-items: center; justify-content: center; gap: 8px; min-width: 126px; min-height: 46px; margin-right: 18px; border: 1px solid var(--line); border-radius: 6px; color: var(--text-primary); font-size: 11px; font-weight: 750; text-decoration: none; }.continue-link:hover { border-color: var(--brand); color: var(--brand); }
.workflow-line { display: flex; align-items: center; justify-content: center; gap: 10px; min-height: 24px; color: var(--text-tertiary); font-size: 9px; }.workflow-line span { display: flex; align-items: center; gap: 5px; }.workflow-line b { color: var(--brand); font-family: "SFMono-Regular", Consolas, monospace; font-size: 8px; }.workflow-line i { width: 24px; height: 1px; background: var(--line); }.workflow-line small { margin-left: 10px; color: var(--text-tertiary); font-size: 9px; }

@keyframes headline-color { 0%, 36%, 100% { color: var(--accent); } 48%, 84% { color: var(--brand); } }

@media (max-width: 1365px) {
  .home-view { min-height: calc(100dvh - 60px); }.landing-page { gap: 14px; }.hero-intro h1 { font-size: 58px; }.hero-description { margin-top: 11px; font-size: 14px; }.parse-workbench { padding: 15px; }.recent-result { min-height: 108px; }.recent-cover { min-height: 106px; }
}

@media (min-width: 768px) and (max-width: 1040px) {
  .hero-intro h1 { font-size: 52px; }.parse-row { grid-template-columns: minmax(0, 1fr) 180px; }.parse-options { grid-template-columns: 260px minmax(0, 1fr); }.support-note { display: none; }
}

@media (max-width: 767px) {
  .home-view { min-height: calc(100dvh - 174px); align-items: start; }.landing-page { gap: 10px; }.hero-intro { justify-items: start; text-align: left; }.hero-intro h1 { font-size: 37px; line-height: 1.12; }.hero-intro h1 span { margin-bottom: 3px; }.hero-description { margin-top: 7px; font-size: 11px; line-height: 1.55; }
  .parse-workbench { padding: 10px; }.parse-workbench::before { left: 18px; width: 54px; }.workbench-heading { margin-bottom: 7px; }.workbench-heading > div:first-child { gap: 7px; }.workbench-heading h2 { font-size: 11px; }.auth-context { display: none; }.parse-row { grid-template-columns: 1fr; gap: 6px; }.source-input { grid-template-columns: 40px minmax(0, 1fr) 44px; height: 46px; }.source-input :deep(.el-input__wrapper) { min-height: 44px; padding-inline: 9px; }.source-input :deep(.el-input__inner) { font-size: 10px; }.source-icon { height: 26px; font-size: 15px; }.paste-button { width: 36px; height: 36px; }.parse-button { height: 46px; min-height: 46px; }.parse-options { grid-template-columns: 1fr; gap: 4px; margin-top: 6px; }.mode-fieldset button { min-height: 44px; }.mode-node { display: none; }.parse-options > p, .support-note { display: none; }.validation { margin-top: 6px; line-height: 1.35; }
  .recent-result { grid-template-columns: 104px minmax(0, 1fr) 44px; min-height: 92px; }.recent-cover { min-height: 90px; }.recent-cover > span { right: 5px; bottom: 5px; padding: 2px 4px; font-size: 7px; }.recent-copy { padding: 10px; }.recent-eyebrow { display: block; overflow: hidden; font-size: 6px; text-overflow: ellipsis; white-space: nowrap; }.recent-copy h2 { display: -webkit-box; margin: 5px 0 6px; overflow: hidden; font-size: 11px; white-space: normal; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }.recent-meta { gap: 6px; font-size: 8px; }.recent-meta span:first-child { display: none; }.continue-link { min-width: 44px; min-height: 44px; margin-right: 5px; border: 0; color: var(--brand); font-size: 17px; }.continue-link span { display: none; }
  .workflow-line { gap: 6px; min-height: 18px; font-size: 7px; }.workflow-line i { width: 10px; }.workflow-line small { display: none; }
}

@media (max-width: 374px) {
  .hero-intro h1 { font-size: 34px; }.hero-description { font-size: 10px; }.recent-result { grid-template-columns: 96px minmax(0, 1fr) 44px; }
}
</style>
