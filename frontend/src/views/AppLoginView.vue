<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Lock, User } from '@element-plus/icons-vue'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import { toApiError } from '@/api/errors'
import { useAppAuthStore } from '@/stores/appAuth'
import { safeReturnPath } from '@/utils/safeReturnPath'

const route = useRoute()
const router = useRouter()
const auth = useAppAuthStore()
const formRef = ref<FormInstance>()
const form = reactive({
  username: '',
  password: '',
  confirmPassword: '',
})

const setupMode = computed(() => !auth.initialized)
const title = computed(() => setupMode.value ? '创建管理员账号' : '管理员登录')
const submitLabel = computed(() => setupMode.value ? '初始化并进入' : '登录')

const rules: FormRules<typeof form> = {
  username: [
    { required: true, message: '请输入管理员用户名', trigger: 'blur' },
    { min: 3, max: 64, message: '用户名长度为 3 至 64 个字符', trigger: 'blur' },
    { pattern: /^[A-Za-z0-9._-]+$/, message: '用户名仅支持字母、数字、点、短横线和下划线', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 12, max: 128, message: '密码长度至少为 12 个字符', trigger: 'blur' },
  ],
  confirmPassword: [{
    validator: (_rule, value: string, callback) => {
      if (!setupMode.value) callback()
      else if (!value) callback(new Error('请再次输入密码'))
      else if (value !== form.password) callback(new Error('两次输入的密码不一致'))
      else callback()
    },
    trigger: 'blur',
  }],
}

async function submit(): Promise<void> {
  if (!await formRef.value?.validate().catch(() => false)) return
  try {
    if (setupMode.value) {
      await auth.setup({
        username: form.username,
        password: form.password,
        confirmPassword: form.confirmPassword,
      })
      ElMessage.success('管理员账号已创建')
    } else {
      await auth.login({ username: form.username, password: form.password })
    }
    const target = safeReturnPath(typeof route.query.returnTo === 'string' ? route.query.returnTo : '/')
    await router.replace(target)
  } catch (reason) {
    const error = toApiError(reason)
    ElMessage.error(error.message)
  }
}
</script>

<template>
  <main class="login-view">
    <section class="brand-panel">
      <div class="brand-mark" aria-hidden="true">
        <span />
      </div>
      <div>
        <strong>Bili Insight</strong>
        <p>本地视频解析、预览、分析与下载工作台</p>
      </div>
      <dl>
        <div><dt>访问边界</dt><dd>本机管理员会话</dd></div>
        <div><dt>账号凭据</dt><dd>Argon2id 本地存储</dd></div>
        <div><dt>平台登录</dt><dd>与 Bilibili Cookie 独立</dd></div>
      </dl>
    </section>

    <section class="form-panel">
      <div class="form-wrap">
        <span class="eyebrow">APPLICATION ACCESS</span>
        <h1>{{ title }}</h1>
        <p>{{ setupMode ? '首次启动需要创建唯一的本机管理员。' : '使用本机管理员账号继续。' }}</p>

        <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
          <el-form-item label="用户名" prop="username">
            <el-input v-model="form.username" :prefix-icon="User" autocomplete="username" size="large" />
          </el-form-item>
          <el-form-item label="密码" prop="password">
            <el-input v-model="form.password" :prefix-icon="Lock" type="password" show-password :autocomplete="setupMode ? 'new-password' : 'current-password'" size="large" @keyup.enter="submit" />
          </el-form-item>
          <el-form-item v-if="setupMode" label="确认密码" prop="confirmPassword">
            <el-input v-model="form.confirmPassword" :prefix-icon="Lock" type="password" show-password autocomplete="new-password" size="large" @keyup.enter="submit" />
          </el-form-item>
          <el-button native-type="submit" type="primary" size="large" :loading="auth.loading">{{ submitLabel }}</el-button>
        </el-form>
      </div>
    </section>
  </main>
</template>

<style scoped>
.login-view { display: grid; grid-template-columns: minmax(360px, .85fr) minmax(520px, 1.15fr); min-height: 100dvh; background: var(--surface); }
.brand-panel { display: flex; flex-direction: column; justify-content: center; padding: clamp(40px, 6vw, 88px); background: #151b2b; color: #fff; }
.brand-mark { display: grid; place-items: center; width: 72px; height: 72px; margin-bottom: 30px; border-radius: 8px; background: #fb7299; }
.brand-mark span { width: 0; height: 0; margin-left: 6px; border-top: 15px solid transparent; border-bottom: 15px solid transparent; border-left: 23px solid #fff; }
.brand-panel strong { font-size: 36px; letter-spacing: 0; }
.brand-panel p { max-width: 420px; margin: 12px 0 0; color: #bac3d5; font-size: 16px; line-height: 1.7; }
.brand-panel dl { display: grid; gap: 1px; margin: 56px 0 0; background: #313a50; }
.brand-panel dl div { display: flex; justify-content: space-between; gap: 20px; padding: 13px 0; background: #151b2b; }
.brand-panel dt { color: #8995aa; }.brand-panel dd { margin: 0; color: #e5e9f2; }
.form-panel { display: grid; place-items: center; padding: 40px; }
.form-wrap { width: min(430px, 100%); }
.eyebrow { color: var(--brand); font-size: 11px; font-weight: 750; }
.form-wrap h1 { margin: 14px 0 8px; font-size: 30px; letter-spacing: 0; }
.form-wrap > p { margin: 0 0 30px; color: var(--text-secondary); }
.form-wrap :deep(.el-form-item) { margin-bottom: 20px; }
.form-wrap .el-button { width: 100%; min-height: 48px; margin-top: 4px; }

@media (max-width: 767px) {
  .login-view { display: block; min-height: 100dvh; }
  .brand-panel { min-height: 220px; padding: max(30px, env(safe-area-inset-top)) 24px 28px; }
  .brand-mark { width: 50px; height: 50px; margin-bottom: 17px; }
  .brand-mark span { border-top-width: 11px; border-bottom-width: 11px; border-left-width: 17px; }
  .brand-panel strong { font-size: 25px; }
  .brand-panel p { font-size: 13px; }
  .brand-panel dl { display: none; }
  .form-panel { display: block; padding: 30px 20px calc(30px + env(safe-area-inset-bottom)); }
  .form-wrap h1 { font-size: 25px; }
}
</style>
