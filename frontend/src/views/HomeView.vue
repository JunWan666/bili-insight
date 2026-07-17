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
    // Store exposes a safe, actionable error below the control deck.
  }
}

function selectMode(mode: AccessMode): void {
  accessMode.value = mode
  validationMessage.value = ''
}
</script>

<template>
  <div class="home-view">
    <section class="xray-home">
      <MediaDecompositionStage :video="previewVideo" :parsing="videos.loading" :source-ready="sourceReady" />

      <div class="home-status">
        <div class="brand-signature"><span>BI / 01</span><i aria-hidden="true" /><strong>BILI INSIGHT</strong><small>VIDEO X-RAY</small></div>
        <AuthStatusBadge :status="auth.status" :loading="auth.loading" />
      </div>

      <header class="hero-copy">
        <span class="hero-kicker">MEDIA INTELLIGENCE WORKSPACE</span>
        <h1>让视频，<strong>显出结构。</strong></h1>
        <p>画面、声音、字幕与元数据，在这里成为一张可操作的媒体图谱。</p>
      </header>

      <div class="parse-panel control-deck">
        <div class="deck-heading">
          <span><b>01</b> INPUT SOURCE</span>
          <div v-if="auth.isAuthenticated && accessMode !== 'authenticated'" class="auth-explanation">
            <el-icon><Lock /></el-icon><span>智能模式优先匿名访问</span>
          </div>
        </div>

        <form novalidate @submit.prevent="parseVideo">
          <h2 class="sr-only">粘贴 Bilibili 视频链接</h2>
          <label class="sr-only" for="video-url">Bilibili 视频链接、BV/AV 号或 ss/ep 标识</label>
          <div class="deck-controls">
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
                <span class="mode-node" aria-hidden="true" />
                <span><strong>{{ mode.title }}</strong><small>{{ mode.description }}</small></span>
              </button>
            </fieldset>

            <el-button class="parse-button" native-type="submit" type="primary" size="large" :loading="videos.loading" data-testid="parse-submit">
              <span>{{ videos.loading ? '正在展开结构' : '开始 X-Ray 解析' }}</span>
              <el-icon v-if="!videos.loading"><ArrowRight /></el-icon>
            </el-button>
          </div>

          <p v-if="validationMessage" class="validation" role="alert"><el-icon><CircleClose /></el-icon>{{ validationMessage }}</p>
        </form>
        <RequestError v-if="videos.error" class="parse-error" :error="videos.error" @retry="parseVideo" />
      </div>

      <footer class="workflow-copy">
        <span>SOURCE</span><i /><span>STREAMS</span><i /><span>INTELLIGENCE</span><i /><span>OUTPUT</span>
        <small>预览、分析和下载都从同一张媒体图谱继续。</small>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.home-view { width: 100%; height: 100%; min-height: 100dvh; overflow: hidden; background: #091113; }
.xray-home { position: relative; width: 100%; height: 100dvh; min-height: 700px; overflow: hidden; background: #091113; color: #edf7f5; }
.xray-home::before { position: absolute; top: 0; bottom: 0; left: 3.8%; width: 3px; background: var(--accent); content: ''; opacity: .85; }
.home-status { position: absolute; top: 38px; right: 4.5%; left: 4.5%; z-index: 10; display: flex; align-items: center; justify-content: space-between; }
.brand-signature { display: flex; align-items: center; gap: 10px; color: #d6e6e3; font-family: "SFMono-Regular", Consolas, monospace; font-size: 9px; }
.brand-signature > span { color: var(--accent); }.brand-signature i { width: 34px; height: 1px; background: rgba(221, 240, 237, .22); }.brand-signature strong { font-size: 9px; }.brand-signature small { color: #718986; font-size: 8px; }
.hero-copy { position: absolute; top: 13%; left: 4.5%; z-index: 8; width: min(42%, 520px); }
.hero-kicker { color: #6f8784; font-family: "SFMono-Regular", Consolas, monospace; font-size: 8px; }
.hero-copy h1 { margin: 17px 0 16px; color: #f5fbfa; font-size: 66px; font-weight: 780; line-height: .95; letter-spacing: 0; }
.hero-copy h1 strong { display: block; color: var(--brand); font-weight: inherit; }
.hero-copy p { max-width: 430px; margin: 0; color: #9bb0ad; font-size: 14px; line-height: 1.7; }
.control-deck { position: absolute; right: 4.5%; bottom: 70px; left: 4.5%; z-index: 12; min-height: 116px; padding: 13px 15px 15px; border: 1px solid rgba(219, 237, 234, .18); border-top: 2px solid var(--brand); border-radius: 6px; background: #f5f7f6; color: #172120; box-shadow: 0 22px 55px rgba(0, 0, 0, .26); }
.deck-heading { display: flex; align-items: center; justify-content: space-between; min-height: 21px; margin-bottom: 8px; color: #657371; font-family: "SFMono-Regular", Consolas, monospace; font-size: 8px; }
.deck-heading b { margin-right: 7px; color: var(--brand); }.auth-explanation { display: flex; align-items: center; gap: 5px; color: #75817f; }
.deck-controls { display: grid; grid-template-columns: minmax(300px, 1fr) minmax(290px, .65fr) minmax(180px, .34fr); gap: 9px; }
.source-input { display: grid; grid-template-columns: 44px minmax(0, 1fr) 44px; align-items: center; min-width: 0; height: 52px; overflow: hidden; border: 1px solid #d7dfdd; border-radius: 5px; background: white; transition: border-color .18s, box-shadow .18s; }
.source-input:focus-within, .source-input.ready { border-color: var(--brand); box-shadow: 0 0 0 2px rgba(12, 127, 121, .1); }.source-input.invalid { border-color: var(--danger); }
.source-icon { display: grid; place-items: center; height: 28px; border-right: 1px solid #e5ebe9; color: var(--brand); font-size: 17px; }
.source-input :deep(.el-input__wrapper) { min-height: 50px; padding-inline: 12px; background: transparent; box-shadow: none; }.source-input :deep(.el-input__inner) { font-size: 12px; }
.paste-button { display: grid; place-items: center; width: 36px; height: 36px; padding: 0; border: 0; border-radius: 5px; background: #eef2f1; color: #677573; cursor: pointer; }.paste-button:hover { background: var(--brand-soft); color: var(--brand); }
.mode-fieldset { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); min-width: 0; padding: 3px; margin: 0; border: 1px solid #d7dfdd; border-radius: 5px; background: #e9eeed; }
.mode-fieldset button { display: flex; align-items: center; justify-content: center; gap: 6px; min-width: 0; min-height: 44px; padding: 4px 6px; border: 1px solid transparent; border-radius: 4px; background: transparent; color: #53615f; cursor: pointer; }
.mode-fieldset button.selected { border-color: #d5dddb; background: white; color: #14201e; box-shadow: 0 4px 12px rgba(25, 41, 39, .06); }.mode-fieldset button.disabled:not(.selected) { opacity: .48; }
.mode-node { flex: 0 0 auto; width: 6px; height: 6px; border: 1px solid #8b9997; border-radius: 50%; }.selected .mode-node { border-color: var(--brand); background: var(--brand); box-shadow: 0 0 0 3px var(--brand-soft); }
.mode-fieldset strong, .mode-fieldset small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.mode-fieldset strong { font-size: 10px; }.mode-fieldset small { display: none; }
.parse-button { width: 100%; height: 52px; min-height: 52px; margin: 0; border-radius: 5px; }.parse-button :deep(span) { display: flex; align-items: center; justify-content: center; gap: 9px; }
.validation { display: flex; align-items: center; gap: 6px; margin: 7px 0 0; color: var(--danger); font-size: 10px; }.parse-error { margin-top: 9px; }
.workflow-copy { position: absolute; right: 4.5%; bottom: 24px; left: 4.5%; z-index: 11; display: flex; align-items: center; gap: 10px; color: #667e7b; font-family: "SFMono-Regular", Consolas, monospace; font-size: 7px; }
.workflow-copy > i { width: 24px; height: 1px; background: rgba(206, 231, 227, .14); }.workflow-copy > span:first-child { color: var(--scene-signal, #39dfc9); }.workflow-copy small { margin-left: auto; color: #718986; font-family: Inter, "PingFang SC", sans-serif; font-size: 9px; }

@media (max-width: 1365px) {
  .hero-copy h1 { font-size: 56px; }
  .deck-controls { grid-template-columns: minmax(270px, 1fr) minmax(260px, .65fr) 180px; }
}

@media (min-width: 768px) and (max-width: 1199px) {
  .xray-home { min-height: 760px; }
  .hero-copy { top: 8%; width: 70%; }.hero-copy h1 { font-size: 54px; }.hero-copy p { max-width: 520px; }
  .deck-controls { grid-template-columns: minmax(260px, 1fr) minmax(260px, .8fr); }
  .parse-button { grid-column: 1 / -1; }
  .control-deck { min-height: 168px; }
}

@media (max-width: 767px) {
  .home-view { min-height: calc(100dvh - 64px); }
  .xray-home { height: calc(100dvh - 64px); min-height: 680px; }
  .xray-home::before { left: 0; width: 2px; }
  .home-status { display: none; }
  .hero-copy { top: 24px; left: 16px; width: calc(100% - 32px); }.hero-kicker { font-size: 6px; }.hero-copy h1 { margin: 8px 0 7px; font-size: 34px; line-height: .98; }.hero-copy p { max-width: 330px; font-size: 10px; line-height: 1.5; }
  .control-deck { right: 12px; bottom: 79px; left: 12px; min-height: 174px; padding: 9px; border-radius: 5px; }
  .deck-heading { margin-bottom: 6px; }.auth-explanation { display: none; }
  .deck-controls { grid-template-columns: 1fr; gap: 6px; }
  .source-input { grid-template-columns: 38px minmax(0, 1fr) 42px; height: 44px; }.source-input :deep(.el-input__wrapper) { min-height: 42px; padding-inline: 8px; }.source-input :deep(.el-input__inner) { font-size: 10px; }.source-icon { height: 24px; font-size: 14px; }.paste-button { width: 34px; height: 34px; }
  .mode-fieldset { gap: 3px; }.mode-fieldset button { min-height: 44px; }.mode-fieldset strong { font-size: 10px; }.mode-node { display: none; }
  .parse-button { height: 46px; min-height: 46px; }
  .validation { position: absolute; right: 9px; bottom: -20px; left: 9px; }
  .workflow-copy { display: none; }
}

@media (max-width: 374px) {
  .hero-copy h1 { font-size: 31px; }
  .hero-copy p { font-size: 9px; }
  .control-deck { bottom: 74px; }
}
</style>
