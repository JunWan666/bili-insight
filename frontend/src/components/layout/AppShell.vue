<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Compass,
  DocumentChecked,
  Files,
  Film,
  Monitor,
  Setting,
} from '@element-plus/icons-vue'
import AuthStatusBadge from '@/components/AuthStatusBadge.vue'
import { useAuthStore } from '@/stores/auth'
import { useJobsStore } from '@/stores/jobs'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const jobs = useJobsStore()

const navigation = [
  { path: '/', label: '解析', icon: Compass, testId: 'nav-home' },
  { path: '/jobs', label: '任务', icon: DocumentChecked, badge: true, testId: 'nav-jobs' },
  { path: '/artifacts', label: '产物', icon: Files, testId: 'nav-artifacts' },
  { path: '/settings', label: '设置', icon: Setting, testId: 'nav-settings' },
]

const activePath = computed(() => {
  if (route.path.startsWith('/videos/')) return '/'
  return navigation.find((item) => route.path.startsWith(item.path) && item.path !== '/')?.path ?? '/'
})

function navigate(path: string): void {
  if (route.path !== path) void router.push(path)
}

function handleVisibility(): void {
  if (document.visibilityState === 'visible') {
    void auth.load(true).catch(() => undefined)
    void jobs.refreshActive()
  }
}

function handlePageHide(): void {
  jobs.dispose()
}

onMounted(() => {
  void auth.load().catch(() => undefined)
  void jobs.refreshActive()
  document.addEventListener('visibilitychange', handleVisibility)
  window.addEventListener('pagehide', handlePageHide)
})

onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', handleVisibility)
  window.removeEventListener('pagehide', handlePageHide)
  jobs.dispose()
})
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <RouterLink class="brand" to="/" aria-label="Bili Insight 首页">
        <span class="brand-mark"><Film /></span>
        <span class="brand-copy"><strong>Bili Insight</strong><small>本地视频工作台</small></span>
      </RouterLink>

      <nav class="desktop-nav" aria-label="主导航">
        <button
          v-for="item in navigation"
          :key="item.path"
          type="button"
          :data-testid="`${item.testId}-desktop`"
          :class="{ active: activePath === item.path }"
          @click="navigate(item.path)"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
          <b v-if="item.badge && jobs.activeCount" class="nav-badge">{{ jobs.activeCount > 99 ? '99+' : jobs.activeCount }}</b>
        </button>
      </nav>

      <div class="sidebar-spacer" />
      <button class="diagnostics-link" type="button" @click="navigate('/diagnostics')">
        <el-icon><Monitor /></el-icon><span>关于与诊断</span>
      </button>
      <RouterLink class="auth-card" to="/settings">
        <AuthStatusBadge :status="auth.status" :loading="auth.loading" compact />
        <small>{{ auth.status?.maskedAccountName || 'Cookie 仅保存在本机服务端' }}</small>
      </RouterLink>
    </aside>

    <header class="mobile-header">
      <RouterLink class="mobile-brand" to="/"><span class="brand-mark"><Film /></span><strong>Bili Insight</strong></RouterLink>
      <RouterLink to="/settings"><AuthStatusBadge :status="auth.status" :loading="auth.loading" compact /></RouterLink>
    </header>

    <main class="main-content">
      <RouterView v-slot="{ Component }">
        <Transition name="page" mode="out-in">
          <component :is="Component" />
        </Transition>
      </RouterView>
    </main>

    <nav class="mobile-nav" aria-label="移动端主导航">
      <button
        v-for="item in navigation"
        :key="item.path"
          type="button"
          :data-testid="`${item.testId}-mobile`"
        :class="{ active: activePath === item.path }"
        @click="navigate(item.path)"
      >
        <span class="mobile-icon">
          <el-icon><component :is="item.icon" /></el-icon>
          <b v-if="item.badge && jobs.activeCount" class="mobile-badge">{{ jobs.activeCount > 9 ? '9+' : jobs.activeCount }}</b>
        </span>
        <small>{{ item.label }}</small>
      </button>
    </nav>
  </div>
</template>

<style scoped>
.app-shell { min-height: 100dvh; }
.sidebar {
  position: fixed;
  inset: 0 auto 0 0;
  z-index: 30;
  display: flex;
  flex-direction: column;
  width: 248px;
  padding: 26px 18px 22px;
  border-right: 1px solid var(--line-soft);
  background: color-mix(in srgb, var(--surface) 94%, transparent);
  backdrop-filter: blur(18px);
}
.brand { display: flex; align-items: center; gap: 12px; padding: 0 8px 30px; color: var(--text-primary); text-decoration: none; }
.brand-mark { display: grid; place-items: center; width: 40px; height: 40px; border-radius: 13px; background: var(--brand); color: white; box-shadow: 0 8px 20px rgba(67, 86, 201, .24); }
.brand-mark svg { width: 22px; height: 22px; }
.brand-copy { display: grid; gap: 2px; }
.brand-copy strong { font-size: 18px; letter-spacing: -.02em; }
.brand-copy small { color: var(--text-tertiary); font-size: 11px; }
.desktop-nav { display: grid; gap: 6px; }
.desktop-nav button, .diagnostics-link {
  display: flex; align-items: center; gap: 13px; width: 100%; min-height: 48px; padding: 0 14px; border: 0; border-radius: 13px;
  background: transparent; color: var(--text-secondary); font: inherit; font-weight: 650; cursor: pointer; transition: .18s ease;
}
.desktop-nav button:hover, .diagnostics-link:hover { background: var(--surface-muted); color: var(--text-primary); }
.desktop-nav button.active { background: var(--brand-soft); color: var(--brand); }
.desktop-nav .el-icon, .diagnostics-link .el-icon { font-size: 20px; }
.nav-badge { margin-left: auto; min-width: 22px; padding: 2px 6px; border-radius: 999px; background: var(--brand); color: #fff; font-size: 11px; text-align: center; }
.sidebar-spacer { flex: 1; }
.diagnostics-link { margin-bottom: 10px; }
.auth-card { display: grid; gap: 8px; padding: 12px; border: 1px solid var(--line-soft); border-radius: 14px; color: inherit; text-decoration: none; }
.auth-card small { color: var(--text-tertiary); line-height: 1.4; overflow-wrap: anywhere; }
.main-content { min-height: 100dvh; margin-left: 248px; padding: 42px clamp(28px, 4vw, 64px) 64px; }
.mobile-header, .mobile-nav { display: none; }
.page-enter-active, .page-leave-active { transition: opacity .16s ease, transform .16s ease; }
.page-enter-from { opacity: 0; transform: translateY(5px); }
.page-leave-to { opacity: 0; transform: translateY(-3px); }

@media (min-width: 768px) and (max-width: 1199px) {
  .sidebar { width: 84px; padding-inline: 12px; }
  .brand { justify-content: center; padding-inline: 0; }
  .brand-copy, .desktop-nav button > span, .diagnostics-link span, .auth-card small, .auth-card :deep(.auth-badge span) { display: none; }
  .desktop-nav button, .diagnostics-link { justify-content: center; padding: 0; }
  .nav-badge { position: absolute; margin: -27px 0 0 27px; }
  .auth-card { place-items: center; padding: 10px 4px; }
  .auth-card :deep(.auth-badge) { width: 32px; justify-content: center; padding: 0; }
  .main-content { margin-left: 84px; }
}

@media (max-width: 767px) {
  .sidebar { display: none; }
  .mobile-header {
    position: sticky; top: 0; z-index: 25; display: flex; align-items: center; justify-content: space-between; min-height: 64px;
    padding: max(10px, env(safe-area-inset-top)) 16px 10px; border-bottom: 1px solid var(--line-soft); background: color-mix(in srgb, var(--surface) 92%, transparent); backdrop-filter: blur(16px);
  }
  .mobile-header a { display: flex; align-items: center; min-height: 44px; color: inherit; text-decoration: none; }
  .mobile-brand { display: flex; align-items: center; gap: 9px; }
  .mobile-brand .brand-mark { width: 34px; height: 34px; border-radius: 11px; }
  .main-content { min-height: calc(100dvh - 64px); margin-left: 0; padding: 22px 16px calc(88px + env(safe-area-inset-bottom)); }
  .mobile-nav {
    position: fixed; inset: auto 0 0; z-index: 40; display: grid; grid-template-columns: repeat(4, 1fr); padding: 7px 8px max(7px, env(safe-area-inset-bottom));
    border-top: 1px solid var(--line-soft); background: color-mix(in srgb, var(--surface) 94%, transparent); backdrop-filter: blur(18px);
  }
  .mobile-nav button { display: grid; place-items: center; gap: 2px; min-height: 52px; border: 0; border-radius: 12px; background: transparent; color: var(--text-tertiary); cursor: pointer; }
  .mobile-nav button.active { color: var(--brand); background: var(--brand-soft); }
  .mobile-icon { position: relative; display: grid; place-items: center; }
  .mobile-icon .el-icon { font-size: 22px; }
  .mobile-nav small { font-size: 11px; font-weight: 650; }
  .mobile-badge { position: absolute; top: -6px; left: 14px; min-width: 17px; padding: 1px 4px; border-radius: 999px; background: #e1495b; color: #fff; font-size: 9px; }
}

@media (max-width: 374px) {
  .main-content { padding-inline: 12px; }
  .mobile-header { padding-inline: 12px; }
  .mobile-header :deep(.auth-badge) { font-size: 11px; }
}
</style>
