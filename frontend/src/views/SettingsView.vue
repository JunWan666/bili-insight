<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Check,
  Delete,
  Files,
  Key,
  Lock,
  Monitor,
  Refresh,
  Setting,
  UploadFilled,
  Warning,
} from '@element-plus/icons-vue'
import { settingsApi } from '@/api'
import { toApiError, type ApiError } from '@/api/errors'
import AuthStatusBadge from '@/components/AuthStatusBadge.vue'
import PageHeader from '@/components/PageHeader.vue'
import RequestError from '@/components/RequestError.vue'
import { isSettingsSection, settingsSections, type SettingsSection } from '@/config/settingsSections'
import { useAuthStore } from '@/stores/auth'
import { useAppAuthStore } from '@/stores/appAuth'
import { useVideosStore } from '@/stores/videos'
import type { AppSettings } from '@/types/api'
import { formatBytes, formatDate } from '@/utils/format'
import { safeVideoReturnPath } from '@/utils/safeReturnPath'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const appAuth = useAppAuthStore()
const videos = useVideosStore()
const activeSection = ref<SettingsSection>('auth')
const settings = ref<AppSettings | null>(null)
const original = ref('')
const settingsLoading = ref(false)
const saving = ref(false)
const settingsError = ref<ApiError | null>(null)
const selectedFile = ref<File | null>(null)
const rememberCookie = ref(false)
const dragActive = ref(false)
const uploadError = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const passwordForm = reactive({ currentPassword: '', newPassword: '', confirmPassword: '' })
const passwordChanging = ref(false)

const dirty = computed(() => settings.value !== null && JSON.stringify(settings.value) !== original.value)
const quotaGb = computed({
  get: () => settings.value?.storage.quotaBytes == null ? null : Number((settings.value.storage.quotaBytes / 1024 ** 3).toFixed(2)),
  set: (value: number | null) => { if (settings.value) settings.value.storage.quotaBytes = value == null ? null : Math.round(value * 1024 ** 3) },
})
const rateLimitMbps = computed({
  get: () => settings.value?.network.rateLimitBytesPerSecond == null ? null : Number((settings.value.network.rateLimitBytesPerSecond / 1024 / 1024).toFixed(2)),
  set: (value: number | null) => { if (settings.value) settings.value.network.rateLimitBytesPerSecond = value == null ? null : Math.round(value * 1024 * 1024) },
})

function setSection(section: SettingsSection): void {
  activeSection.value = section
  const query = { ...route.query }
  if (section === 'auth') delete query.section
  else query.section = section
  void router.replace({ query })
}

function handleSectionChange(section: string): void {
  if (isSettingsSection(section)) setSection(section)
}

async function changePassword(): Promise<void> {
  if (passwordForm.newPassword.length < 12) {
    ElMessage.warning('新密码长度至少为 12 个字符')
    return
  }
  if (passwordForm.newPassword !== passwordForm.confirmPassword) {
    ElMessage.warning('两次输入的新密码不一致')
    return
  }
  passwordChanging.value = true
  try {
    await appAuth.changePassword(passwordForm)
    passwordForm.currentPassword = ''
    passwordForm.newPassword = ''
    passwordForm.confirmPassword = ''
    ElMessage.success('管理员密码已更新，其他会话已经失效')
  } catch (reason) {
    ElMessage.error(toApiError(reason).message)
  } finally {
    passwordChanging.value = false
  }
}

async function loadSettings(): Promise<void> {
  settingsLoading.value = true
  settingsError.value = null
  try {
    settings.value = await settingsApi.get()
    original.value = JSON.stringify(settings.value)
  } catch (reason) {
    settingsError.value = toApiError(reason)
  } finally { settingsLoading.value = false }
}

function validateFile(file: File): boolean {
  uploadError.value = ''
  if (file.size > 1024 * 1024) uploadError.value = 'Cookie 文件不能超过 1 MB'
  else if (!file.name.toLowerCase().endsWith('.json')) uploadError.value = '只接受 .json 格式的 Cookie 文件'
  else if (file.type && !['application/json', 'text/json', 'application/octet-stream'].includes(file.type)) uploadError.value = '文件类型不是可识别的 JSON'
  if (uploadError.value) { selectedFile.value = null; return false }
  selectedFile.value = file
  return true
}

function selectFile(event: Event): void {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) validateFile(file)
  input.value = ''
}

function dropFile(event: DragEvent): void {
  dragActive.value = false
  const file = event.dataTransfer?.files[0]
  if (file) validateFile(file)
}

async function uploadCookie(): Promise<void> {
  if (!selectedFile.value || !validateFile(selectedFile.value)) {
    if (!selectedFile.value) uploadError.value = '请先选择 Cookie JSON 文件'
    return
  }
  try {
    await auth.upload(selectedFile.value, rememberCookie.value)
    selectedFile.value = null
    ElMessage.success(auth.isPremium ? 'Cookie 校验成功，大会员有效' : 'Cookie 校验成功，已登录')
    const returnPath = safeVideoReturnPath(route.query.returnTo)
    if (returnPath) {
      const target = router.resolve(returnPath)
      await router.replace({
        path: target.path,
        query: { ...target.query, useAuth: '1' },
        hash: target.hash,
      })
    }
  } catch (reason) {
    uploadError.value = toApiError(reason).message
  }
}

async function validateCookie(): Promise<void> {
  try { await auth.validate(); ElMessage.success('登录状态校验完成') }
  catch (reason) { ElMessage.error(toApiError(reason).message) }
}

async function clearCookie(): Promise<void> {
  try {
    await ElMessageBox.confirm('将彻底删除内存 Cookie、本机加密密文与身份缓存。已完成的下载文件不会被删除。', '清除登录态', { type: 'warning', confirmButtonText: '彻底清除', cancelButtonText: '取消' })
    await auth.clear()
    videos.clearAuthenticatedContext()
    selectedFile.value = null
    rememberCookie.value = false
    ElMessage.success('登录态已彻底清除，后续请求将使用匿名模式')
  } catch (reason) {
    if (reason !== 'cancel' && reason !== 'close') ElMessage.error(toApiError(reason).message)
  }
}

async function saveSettings(): Promise<void> {
  if (!settings.value) return
  saving.value = true
  settingsError.value = null
  try {
    settings.value = await settingsApi.update(settings.value)
    original.value = JSON.stringify(settings.value)
    ElMessage.success('设置已保存')
  } catch (reason) { settingsError.value = toApiError(reason) }
  finally { saving.value = false }
}

watch(() => route.query.section, (section) => {
  activeSection.value = isSettingsSection(section) ? section : 'auth'
}, { immediate: true })

onMounted(() => { void auth.load(); void loadSettings() })
</script>

<template>
  <div class="settings-view">
    <PageHeader title="设置" description="身份凭据由服务端安全管理；下载、存储与分析偏好对后续任务生效。" eyebrow="PREFERENCES">
      <template #actions><el-button :icon="Monitor" @click="$router.push('/diagnostics')">关于与诊断</el-button></template>
    </PageHeader>

    <div class="settings-section-switcher surface-card">
      <div>
        <span>设置分组</span>
        <small>{{ settingsSections.find((section) => section.value === activeSection)?.description }}</small>
      </div>
      <el-select :model-value="activeSection" data-testid="settings-section-select" aria-label="设置分组" @change="handleSectionChange">
        <el-option v-for="section in settingsSections" :key="section.value" :label="section.label" :value="section.value" />
      </el-select>
    </div>

    <div class="settings-layout">
      <main class="settings-panel surface-card">
        <section v-if="activeSection === 'account'" class="settings-section">
          <div class="section-head"><div><h2>本机管理员</h2><p>该账号用于保护 Bili Insight 页面、任务、产物和 Cookie 设置，与 Bilibili 登录态相互独立。</p></div><el-tag type="success" effect="plain">{{ appAuth.status?.username }}</el-tag></div>
          <div class="form-grid account-password-form">
            <label class="wide"><span>当前密码</span><el-input v-model="passwordForm.currentPassword" type="password" show-password autocomplete="current-password" /></label>
            <label><span>新密码</span><el-input v-model="passwordForm.newPassword" type="password" show-password autocomplete="new-password" /></label>
            <label><span>确认新密码</span><el-input v-model="passwordForm.confirmPassword" type="password" show-password autocomplete="new-password" /></label>
          </div>
          <div class="account-actions"><el-button type="primary" :icon="Lock" :loading="passwordChanging" @click="changePassword">更新管理员密码</el-button></div>
        </section>

        <section v-else-if="activeSection === 'auth'" class="settings-section auth-section">
          <div class="section-head"><div><h2>Cookie 登录态</h2><p>上传常见浏览器扩展导出的 Cookie JSON。系统不会索取账号密码，也不会在浏览器内保存或回显 Cookie 原文。</p></div><AuthStatusBadge :status="auth.status" :loading="auth.loading" /></div>

          <div v-if="auth.status?.isAuthenticated" class="account-card">
            <span class="account-icon"><Check /></span>
            <div><small>当前 Bilibili 身份</small><strong>{{ auth.status.maskedAccountName || '已脱敏账号' }}</strong><p>{{ auth.status.membershipType || (auth.status.isPremium ? '大会员有效' : '普通会员') }} · {{ auth.status.remembered ? '本机加密记住' : '仅本次会话' }}</p></div>
            <dl><div><dt>最近校验</dt><dd>{{ formatDate(auth.status.lastValidatedAt) }}</dd></div><div><dt>Cookie 到期</dt><dd>{{ formatDate(auth.status.cookieExpiresAt) }}</dd></div></dl>
          </div>

          <div v-else-if="auth.status?.status === 'expired' || auth.status?.status === 'error'" class="auth-warning"><el-icon><Warning /></el-icon><div><strong>{{ auth.status.status === 'expired' ? '登录状态已失效' : '登录验证暂时异常' }}</strong><p>{{ auth.status.message || (auth.status.status === 'expired' ? '当前已安全降级为匿名模式，请重新上传。' : '原配置已保留，可稍后重新校验。') }}</p></div></div>

          <div class="auth-workspace">
            <div class="privacy-alert"><el-icon><Lock /></el-icon><div><strong>Cookie 等同账号会话凭据</strong><p>仅上传你自己的 Cookie 文件。原始上传文件在服务端解析后立即删除；凭据只会按域和 path 规则发送给允许的 Bilibili 服务。</p></div></div>

            <div class="auth-upload-column">
              <div class="upload-area" :class="{ dragging: dragActive, selected: selectedFile }" @dragenter.prevent="dragActive = true" @dragover.prevent="dragActive = true" @dragleave.prevent="dragActive = false" @drop.prevent="dropFile">
                <input ref="fileInput" class="sr-only" type="file" accept=".json,application/json,text/json" data-testid="cookie-file-input" @change="selectFile">
                <el-icon><UploadFilled /></el-icon>
                <template v-if="selectedFile"><strong>{{ selectedFile.name }}</strong><p>{{ formatBytes(selectedFile.size) }} · 文件内容不会在前端读取</p><el-button @click="fileInput?.click()">更换文件</el-button></template>
                <template v-else><strong>拖入 Cookie JSON，或从设备选择</strong><p>仅接受 JSON，最大 1 MB；移动端会打开系统文件选择器。</p><el-button type="primary" plain @click="fileInput?.click()">选择文件</el-button></template>
              </div>
              <p v-if="uploadError" class="upload-error" role="alert">{{ uploadError }}</p>
            </div>

            <label class="remember-row"><span><strong>在本机记住登录态</strong><small>关闭时仅在本次服务会话使用；开启后由服务端主密钥认证加密保存。</small></span><el-switch v-model="rememberCookie" /></label>

            <div class="auth-actions">
              <el-button type="primary" :loading="auth.loading" :disabled="!selectedFile" :icon="Key" data-testid="cookie-upload-submit" @click="uploadCookie">{{ auth.isAuthenticated ? '校验并替换 Cookie' : '上传并校验' }}</el-button>
              <el-button v-if="auth.status && auth.status.status !== 'anonymous'" :loading="auth.loading" :icon="Refresh" @click="validateCookie">重新校验</el-button>
              <el-button v-if="auth.status && auth.status.status !== 'anonymous'" type="danger" plain :loading="auth.loading" :icon="Delete" @click="clearCookie">彻底清除</el-button>
            </div>
          </div>
        </section>

        <template v-else>
          <RequestError v-if="settingsError && !settings" :error="settingsError" @retry="loadSettings" />
          <div v-else-if="settingsLoading && !settings" class="settings-loading"><el-skeleton :rows="7" animated /></div>
          <template v-else-if="settings">
            <section v-if="activeSection === 'download'" class="settings-section">
              <div class="section-head"><div><h2>下载默认值</h2><p>这些选项会预填到新任务，仍可在每次下载前单独调整。</p></div></div>
              <div class="form-grid">
                <label><span>默认预设</span><el-select v-model="settings.download.defaultPreset"><el-option label="最佳画质" value="best_quality" /><el-option label="最佳兼容" value="best_compatibility" /><el-option label="最小体积" value="smallest" /><el-option label="仅音频" value="audio_only" /><el-option label="自定义" value="custom" /></el-select></label>
                <label><span>默认封装</span><el-select v-model="settings.download.defaultContainer"><el-option label="MP4" value="mp4" /><el-option label="MKV" value="mkv" /></el-select></label>
                <label><span>最小体积的最低分辨率</span><el-select v-model="settings.download.minimumResolutionHeight"><el-option label="不限" :value="null" /><el-option label="360P" :value="360" /><el-option label="480P" :value="480" /><el-option label="720P" :value="720" /><el-option label="1080P" :value="1080" /></el-select><small>仅用于“最小体积”预设；无满足规格时会明确提示并回退。</small></label>
                <label><span>下载并发数</span><el-input-number v-model="settings.download.concurrency" :min="1" :max="4" controls-position="right" /></label>
                <label><span>失败重试次数</span><el-input-number v-model="settings.download.retryLimit" :min="0" :max="5" controls-position="right" /></label>
                <label class="wide"><span>默认文件名模板</span><el-input v-model="settings.download.filenameTemplate" maxlength="180" /><small>支持 {title}、{bvid}、{page}、{part}、{quality}</small></label>
              </div>
            </section>

            <section v-else-if="activeSection === 'storage'" class="settings-section">
              <div class="section-head"><div><h2>存储与清理</h2><p>目录在服务端本机生效，下载请求本身不能指定任意绝对路径。</p></div></div>
              <div class="form-grid">
                <label class="wide"><span>产物目录</span><el-input v-model="settings.storage.artifactDirectory" :prefix-icon="Files" /></label>
                <label class="wide"><span>临时目录</span><el-input v-model="settings.storage.temporaryDirectory" :prefix-icon="Files" /></label>
                <label><span>磁盘配额（GB）</span><el-input-number v-model="quotaGb" :min="1" :max="100000" :precision="1" controls-position="right" /><small>留空表示不设置应用配额</small></label>
                <label><span>自动清理周期（天）</span><el-input-number v-model="settings.storage.cleanupAfterDays" :min="1" :max="3650" controls-position="right" /><small>留空表示手动删除</small></label>
              </div>
            </section>

            <section v-else-if="activeSection === 'analysis'" class="settings-section">
              <div class="section-head"><div><h2>分析模型</h2><p>模型是否可用以诊断页的实际健康状态为准；未安装的能力会给出明确错误。</p></div></div>
              <div class="form-grid">
                <label><span>默认语言</span><el-select v-model="settings.analysis.language"><el-option label="中文（简体）" value="zh-CN" /><el-option label="自动检测" value="auto" /><el-option label="英语" value="en" /><el-option label="日语" value="ja" /></el-select></label>
                <label><span>ASR 模型</span><el-select v-model="settings.analysis.asrModel"><el-option label="Tiny" value="tiny" /><el-option label="Base" value="base" /><el-option label="Small" value="small" /><el-option label="Medium" value="medium" /><el-option label="Large v3" value="large-v3" /></el-select></label>
                <label><span>计算设备</span><el-select v-model="settings.analysis.device"><el-option label="自动" value="auto" /><el-option label="CPU" value="cpu" /><el-option label="GPU" value="gpu" /></el-select></label>
                <label><span>画面采样间隔（秒）</span><el-input-number v-model="settings.analysis.sampleIntervalSeconds" :min="0.2" :max="60" :step="0.5" :precision="1" controls-position="right" /></label>
                <label><span>最长分析时长（秒）</span><el-input-number v-model="settings.analysis.maximumDurationSeconds" :min="60" :max="86400" controls-position="right" /></label>
                <label class="switch-field"><span><strong>启用 OCR 能力</strong><small>需要服务端安装并配置 PaddleOCR</small></span><el-switch v-model="settings.analysis.ocrEnabled" /></label>
              </div>
            </section>

            <section v-else-if="activeSection === 'network'" class="settings-section">
              <div class="section-head"><div><h2>网络策略</h2><p>合理的请求间隔和并发有助于避免触发上游限流；工具不会绕过平台风控。</p></div></div>
              <div class="form-grid">
                <label><span>请求超时（秒）</span><el-input-number v-model="settings.network.timeoutSeconds" :min="5" :max="300" controls-position="right" /></label>
                <label><span>下载限速（MB/s）</span><el-input-number v-model="rateLimitMbps" :min="0.1" :max="10000" :precision="1" controls-position="right" /><small>留空表示不主动限速</small></label>
                <label><span>上游请求最小间隔（毫秒）</span><el-input-number v-model="settings.network.upstreamIntervalMilliseconds" :min="0" :max="60000" :step="100" controls-position="right" /></label>
              </div>
            </section>

            <section v-else-if="activeSection === 'privacy'" class="settings-section">
              <div class="section-head"><div><h2>隐私与历史</h2><p>诊断始终脱敏，不包含 Cookie、签名 URL、账号标识或服务器绝对路径。</p></div></div>
              <div class="form-grid">
                <label><span>历史保留时间（天）</span><el-input-number v-model="settings.privacy.historyRetentionDays" :min="1" :max="3650" controls-position="right" /><small>到期后清除任务、分析与视频元数据；产物文件转为受管保留，是否自动删文件由“产物清理周期”决定。留空表示不自动清历史。</small></label>
                <label class="switch-field"><span><strong>收集本机诊断指标</strong><small>队列长度、失败率、耗时和磁盘空间，不含敏感值</small></span><el-switch v-model="settings.privacy.diagnosticsEnabled" /></label>
              </div>
            </section>

            <RequestError v-if="settingsError" class="save-error" :error="settingsError" />
            <div class="save-bar"><span>{{ dirty ? '有未保存的更改' : '所有更改均已保存' }}</span><el-button type="primary" :loading="saving" :disabled="!dirty" :icon="Setting" @click="saveSettings">保存设置</el-button></div>
          </template>
        </template>
      </main>
    </div>
  </div>
</template>

<style scoped>
.settings-view { width: 100%; }.settings-layout { width: 100%; }.settings-section-switcher { display: none; }
.settings-panel { min-width: 0; overflow: clip; }.settings-section { padding: 22px; }.section-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; margin-bottom: 17px; padding-bottom: 15px; border-bottom: 1px solid var(--line-soft); }.section-head h2 { margin: 0; font-size: 20px; }.section-head p { max-width: 720px; margin: 5px 0 0; color: var(--text-secondary); line-height: 1.55; }
.account-card { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 14px; margin-bottom: 17px; padding: 16px; border: 1px solid #a9ddca; border-radius: 14px; background: #effaf5; }.account-icon { display: grid; place-items: center; width: 40px; height: 40px; border-radius: 50%; background: #d4f1e4; color: var(--success); }.account-icon svg { width: 19px; }.account-card small, .account-card strong, .account-card p { display: block; margin: 0; }.account-card > div > small { color: #4f8c76; font-size: 10px; }.account-card > div > strong { margin-top: 4px; }.account-card p { margin-top: 4px; color: #4f7869; font-size: 11px; }.account-card dl { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin: 0; }.account-card dt { color: #62927f; font-size: 9px; }.account-card dd { margin: 4px 0 0; color: #386c59; font-size: 10px; }
.auth-warning, .privacy-alert { display: flex; align-items: flex-start; gap: 11px; margin-bottom: 14px; padding: 13px; border-radius: 12px; }.auth-warning { background: #fff2e8; color: #99521d; }.privacy-alert { background: var(--brand-soft); color: var(--brand); }.auth-warning p, .privacy-alert p { margin: 4px 0 0; color: var(--text-secondary); font-size: 11px; line-height: 1.55; }
.upload-area { display: grid; place-items: center; min-height: 210px; padding: 25px; border: 1.5px dashed var(--line); border-radius: 15px; background: var(--surface-muted); text-align: center; transition: .16s; }.upload-area.dragging { border-color: var(--brand); background: var(--brand-soft); }.upload-area.selected { border-style: solid; }.upload-area > .el-icon { margin-bottom: 10px; color: var(--brand); font-size: 42px; }.upload-area strong { overflow-wrap: anywhere; }.upload-area p { margin: 7px 0 14px; color: var(--text-tertiary); font-size: 11px; }.upload-error { margin: 9px 0 0; color: var(--danger); }
.auth-workspace { display: grid; gap: 14px; }.remember-row, .switch-field { display: flex; align-items: center; justify-content: space-between; gap: 22px; }.remember-row { padding: 13px; border: 1px solid var(--line-soft); border-radius: 12px; }.remember-row strong, .remember-row small, .switch-field strong, .switch-field small { display: block; }.remember-row strong, .switch-field strong { font-size: 12px; }.remember-row small, .switch-field small { margin-top: 4px; color: var(--text-tertiary); font-size: 10px; line-height: 1.5; }.auth-actions { display: flex; flex-wrap: wrap; gap: 8px; }.auth-actions .el-button { margin-left: 0; }
.settings-loading { padding: 30px; }.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }.form-grid > label { display: grid; align-content: start; gap: 7px; min-width: 0; }.form-grid > label > span { color: var(--text-secondary); font-size: 11px; font-weight: 650; }.form-grid > label > small { color: var(--text-tertiary); font-size: 10px; }.form-grid .wide { grid-column: 1 / -1; }.form-grid :deep(.el-input-number) { width: 100%; }.form-grid .switch-field { display: flex; min-height: 60px; padding: 11px; border: 1px solid var(--line-soft); border-radius: 11px; }.account-actions { display: flex; justify-content: flex-end; margin-top: 20px; }
.save-error { margin: 0 28px 15px; }.save-bar { position: sticky; bottom: 0; z-index: 5; display: flex; align-items: center; justify-content: flex-end; gap: 16px; padding: 14px 28px; border-top: 1px solid var(--line-soft); background: color-mix(in srgb, var(--surface) 96%, transparent); backdrop-filter: blur(14px); }.save-bar span { margin-right: auto; color: var(--text-tertiary); font-size: 11px; }
@media (min-width: 1200px) {
  .auth-workspace { grid-template-columns: minmax(250px, .8fr) minmax(350px, 1.2fr); grid-template-rows: auto auto 1fr; column-gap: 18px; }
  .auth-workspace .privacy-alert { grid-column: 1; grid-row: 1; margin: 0; }
  .auth-upload-column { grid-column: 2; grid-row: 1 / span 3; }
  .auth-workspace .remember-row { grid-column: 1; grid-row: 2; }
  .auth-workspace .auth-actions { grid-column: 1; grid-row: 3; align-self: end; }
  .form-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 1199px) {
  .settings-section-switcher { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 12px; padding: 10px 12px; }
  .settings-section-switcher > div { min-width: 0; }.settings-section-switcher span, .settings-section-switcher small { display: block; }.settings-section-switcher span { font-size: 12px; font-weight: 750; }.settings-section-switcher small { margin-top: 2px; overflow: hidden; color: var(--text-tertiary); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
  .settings-section-switcher :deep(.el-select) { width: min(220px, 48%); }
  .settings-section-switcher :deep(.el-select__wrapper) { min-height: 44px; }
}
@media (max-width: 900px) { .account-card { grid-template-columns: auto 1fr; }.account-card dl { grid-column: 2; } }
@media (max-width: 767px) {
  .settings-section-switcher { position: sticky; top: 66px; z-index: 12; }.settings-section-switcher > div small { display: none; }.settings-section-switcher :deep(.el-select) { width: 54%; }
  .settings-panel { border-radius: 16px; }.section-head { display: block; }.section-head :deep(.auth-badge) { margin-top: 12px; }.account-card { grid-template-columns: auto 1fr; padding: 13px; }.account-card dl { grid-column: 1 / -1; }.form-grid { grid-template-columns: 1fr; }.form-grid .wide { grid-column: auto; }.auth-actions { display: grid; grid-template-columns: 1fr; }.auth-actions .el-button { min-height: 46px; margin: 0; }.save-bar { bottom: 68px; padding: 12px 16px; }.save-bar span { display: none; }.save-bar .el-button { width: 100%; min-height: 46px; }
}
</style>
