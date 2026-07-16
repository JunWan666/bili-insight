<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Component as VueComponent } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Compass,
  Clock,
  ArrowDown,
  Connection,
  Cpu,
  DocumentChecked,
  Download,
  Expand,
  Files,
  Film,
  Fold,
  Key,
  Lock,
  Monitor,
  Setting,
  SwitchButton,
  User,
} from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import AuthStatusBadge from '@/components/AuthStatusBadge.vue'
import { isSettingsSection, settingsSectionPath, settingsSections, type SettingsSection } from '@/config/settingsSections'
import { useAuthStore } from '@/stores/auth'
import { useAppAuthStore } from '@/stores/appAuth'
import { useJobsStore } from '@/stores/jobs'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const appAuth = useAppAuthStore()
const jobs = useJobsStore()
const sidebarCollapsed = ref(window.localStorage.getItem('bili-insight:sidebar-collapsed') === 'true')
const settingsExpanded = ref(
  route.path.startsWith('/settings') || window.localStorage.getItem('bili-insight:settings-expanded') === 'true',
)

const navigation = [
  { path: '/', label: '解析', icon: Compass, testId: 'nav-home' },
  { path: '/recent', label: '最近', icon: Clock, testId: 'nav-recent' },
  { path: '/jobs', label: '任务', icon: DocumentChecked, badge: true, testId: 'nav-jobs' },
  { path: '/artifacts', label: '产物', icon: Files, testId: 'nav-artifacts' },
]
const settingsNavigation = { path: '/settings/auth', label: '设置', icon: Setting, testId: 'nav-settings' }
const mobileNavigation = [...navigation, settingsNavigation]
const settingsSectionIcons: Record<SettingsSection, VueComponent> = {
  account: User,
  auth: Key,
  download: Download,
  storage: Files,
  analysis: Cpu,
  network: Connection,
  privacy: Lock,
}

const activePath = computed(() => {
  if (route.path.startsWith('/videos/')) return '/'
  if (route.path.startsWith('/settings')) return '/settings'
  return navigation.find((item) => route.path.startsWith(item.path) && item.path !== '/')?.path ?? '/'
})
const activeSettingsSection = computed<SettingsSection>(() => (
  isSettingsSection(route.path.split('/')[2]) ? route.path.split('/')[2] as SettingsSection : 'auth'
))

function navigate(path: string): void {
  if (route.path !== path) void router.push(path)
}

function toggleSidebar(): void {
  sidebarCollapsed.value = !sidebarCollapsed.value
  window.localStorage.setItem('bili-insight:sidebar-collapsed', String(sidebarCollapsed.value))
}

function toggleSettings(): void {
  if (sidebarCollapsed.value) {
    void router.push('/settings/auth')
    return
  }
  if (!route.path.startsWith('/settings')) {
    settingsExpanded.value = true
    void router.push('/settings/auth')
    return
  }
  settingsExpanded.value = !settingsExpanded.value
}

function navigateSettings(section: SettingsSection): void {
  settingsExpanded.value = true
  void router.push({ path: settingsSectionPath(section), query: route.query })
}

watch(settingsExpanded, (expanded) => {
  window.localStorage.setItem('bili-insight:settings-expanded', String(expanded))
})

function handleVisibility(): void {
  if (document.visibilityState === 'visible') {
    void auth.load(true).catch(() => undefined)
    void jobs.refreshActive()
  }
}

function handlePageHide(): void {
  jobs.dispose()
}

async function logout(): Promise<void> {
  try {
    await ElMessageBox.confirm('退出后需要重新输入本机管理员密码。', '退出 Bili Insight', {
      confirmButtonText: '退出',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  await appAuth.logout()
  jobs.dispose()
  await router.replace({ name: 'app-login' })
}

function handleSessionExpired(): void {
  appAuth.expire()
  jobs.dispose()
  if (route.name !== 'app-login') void router.replace({ name: 'app-login', query: { returnTo: route.fullPath } })
}

onMounted(() => {
  void auth.load().catch(() => undefined)
  void jobs.refreshActive()
  document.addEventListener('visibilitychange', handleVisibility)
  window.addEventListener('pagehide', handlePageHide)
  window.addEventListener('bili-insight:session-expired', handleSessionExpired)
})

onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', handleVisibility)
  window.removeEventListener('pagehide', handlePageHide)
  window.removeEventListener('bili-insight:session-expired', handleSessionExpired)
  jobs.dispose()
})
</script>

<template>
  <div class="app-shell" :class="{ 'sidebar-collapsed': sidebarCollapsed }">
    <aside class="sidebar" :class="{ 'is-collapsed': sidebarCollapsed }">
      <RouterLink class="brand" to="/" aria-label="Bili Insight 首页">
        <span class="brand-mark"><Film /></span>
        <span class="brand-copy"><strong>Bili Insight</strong><small>本地视频工作台</small></span>
      </RouterLink>

      <el-tooltip :content="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'" placement="right">
        <button class="sidebar-toggle" type="button" data-testid="sidebar-toggle" :aria-label="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'" @click="toggleSidebar">
          <el-icon><Expand v-if="sidebarCollapsed" /><Fold v-else /></el-icon>
        </button>
      </el-tooltip>

      <nav class="desktop-nav" aria-label="主导航">
        <el-tooltip
          v-for="item in navigation"
          :key="item.path"
          :disabled="!sidebarCollapsed"
          :content="item.label"
          placement="right"
        >
          <button
            type="button"
            :data-testid="`${item.testId}-desktop`"
            :class="{ active: activePath === item.path }"
            @click="navigate(item.path)"
          >
            <el-icon><component :is="item.icon" /></el-icon>
            <span>{{ item.label }}</span>
            <b v-if="item.badge && jobs.activeCount" class="nav-badge">{{ jobs.activeCount > 99 ? '99+' : jobs.activeCount }}</b>
          </button>
        </el-tooltip>
        <div class="settings-nav-group" :class="{ open: settingsExpanded && !sidebarCollapsed }">
          <el-tooltip :disabled="!sidebarCollapsed" content="设置" placement="right">
            <button
              type="button"
              class="settings-parent"
              data-testid="nav-settings-desktop"
              :class="{ active: activePath === '/settings' }"
              :aria-expanded="settingsExpanded && !sidebarCollapsed"
              @click="toggleSettings"
            >
              <el-icon><Setting /></el-icon>
              <span>设置</span>
              <el-icon class="expand-indicator"><ArrowDown /></el-icon>
            </button>
          </el-tooltip>
          <div v-show="settingsExpanded && !sidebarCollapsed" class="settings-subnav" data-testid="settings-subnav">
            <button
              v-for="section in settingsSections"
              :key="section.value"
              type="button"
              :data-testid="`settings-section-${section.value}-desktop`"
              :class="{ active: activePath === '/settings' && activeSettingsSection === section.value }"
              @click="navigateSettings(section.value)"
            >
              <el-icon><component :is="settingsSectionIcons[section.value]" /></el-icon>
              <span>{{ section.label }}</span>
            </button>
          </div>
        </div>
      </nav>

      <div class="sidebar-spacer" />
      <div class="admin-session">
        <el-icon><User /></el-icon>
        <span><small>本机管理员</small><strong>{{ appAuth.status?.username }}</strong></span>
        <el-tooltip content="退出登录" placement="right"><button type="button" aria-label="退出登录" @click="logout"><SwitchButton /></button></el-tooltip>
      </div>
      <el-tooltip :disabled="!sidebarCollapsed" content="关于与诊断" placement="right">
        <button class="diagnostics-link" type="button" @click="navigate('/diagnostics')">
          <el-icon><Monitor /></el-icon><span>关于与诊断</span>
        </button>
      </el-tooltip>
      <RouterLink class="auth-card" to="/settings/auth">
        <AuthStatusBadge :status="auth.status" :loading="auth.loading" compact />
        <small>{{ auth.status?.maskedAccountName || 'Cookie 仅保存在本机服务端' }}</small>
      </RouterLink>
    </aside>

    <header class="mobile-header">
      <RouterLink class="mobile-brand" to="/"><span class="brand-mark"><Film /></span><strong>Bili Insight</strong></RouterLink>
      <RouterLink to="/settings/auth"><AuthStatusBadge :status="auth.status" :loading="auth.loading" compact /></RouterLink>
    </header>

    <main class="main-content" :class="{ 'is-video-workspace': route.path.startsWith('/videos/'), 'is-sidebar-collapsed': sidebarCollapsed }">
      <RouterView v-slot="{ Component }">
        <Transition name="page" mode="out-in">
          <component :is="Component" />
        </Transition>
      </RouterView>
    </main>

    <nav class="mobile-nav" aria-label="移动端主导航">
      <button
        v-for="item in mobileNavigation"
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
  transition: width .2s ease, padding .2s ease;
}
.sidebar-toggle { position: absolute; top: 31px; right: -15px; z-index: 2; display: grid; place-items: center; width: 30px; height: 30px; padding: 0; border: 1px solid var(--line); border-radius: 50%; background: var(--surface); color: var(--text-secondary); box-shadow: 0 5px 14px rgba(31, 36, 51, .12); cursor: pointer; }
.sidebar-toggle:hover { color: var(--brand); border-color: var(--brand); }
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
.settings-nav-group { display: grid; gap: 4px; }
.settings-parent .expand-indicator { margin-left: auto; font-size: 14px; transition: transform .18s ease; }
.settings-nav-group.open .expand-indicator { transform: rotate(180deg); }
.settings-subnav { display: grid; gap: 4px; padding: 0 0 2px 12px; }
.desktop-nav .settings-subnav button { min-height: 42px; padding: 0 14px; border-radius: 11px; font-size: 12px; }
.settings-subnav .el-icon { font-size: 17px; }
.nav-badge { margin-left: auto; min-width: 22px; padding: 2px 6px; border-radius: 999px; background: var(--brand); color: #fff; font-size: 11px; text-align: center; }
.sidebar-spacer { flex: 1; }
.admin-session { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 9px; margin-bottom: 9px; padding: 10px 11px; border-top: 1px solid var(--line-soft); border-bottom: 1px solid var(--line-soft); }
.admin-session > .el-icon { color: var(--brand); }.admin-session span { min-width: 0; }.admin-session small, .admin-session strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.admin-session small { color: var(--text-tertiary); font-size: 9px; }.admin-session strong { margin-top: 2px; font-size: 11px; }.admin-session button { display: grid; place-items: center; width: 32px; height: 32px; border: 0; background: transparent; color: var(--text-secondary); cursor: pointer; }.admin-session button:hover { color: var(--danger); }
.diagnostics-link { margin-bottom: 10px; }
.auth-card { display: grid; gap: 8px; padding: 12px; border: 1px solid var(--line-soft); border-radius: 14px; color: inherit; text-decoration: none; }
.auth-card small { color: var(--text-tertiary); line-height: 1.4; overflow-wrap: anywhere; }
.main-content { min-height: 100dvh; margin-left: 248px; padding: 42px clamp(28px, 4vw, 64px) 64px; transition: margin-left .2s ease; }
.sidebar.is-collapsed { width: 84px; padding-inline: 12px; }
.sidebar.is-collapsed .brand { justify-content: center; padding-inline: 0; }
.sidebar.is-collapsed .brand-copy,
.sidebar.is-collapsed .desktop-nav button > span,
.sidebar.is-collapsed .settings-subnav,
.sidebar.is-collapsed .settings-parent .expand-indicator,
.sidebar.is-collapsed .diagnostics-link span,
.sidebar.is-collapsed .auth-card small,
.sidebar.is-collapsed .auth-card :deep(.auth-badge span),
.sidebar.is-collapsed .admin-session span { display: none; }
.sidebar.is-collapsed .admin-session { display: flex; justify-content: center; padding-inline: 0; }
.sidebar.is-collapsed .admin-session > .el-icon { display: none; }
.sidebar.is-collapsed .desktop-nav button,
.sidebar.is-collapsed .diagnostics-link { justify-content: center; padding: 0; }
.sidebar.is-collapsed .nav-badge { position: absolute; margin: -27px 0 0 27px; }
.sidebar.is-collapsed .auth-card { width: 48px; min-height: 48px; margin-inline: auto; place-items: center; padding: 0; border: 0; background: transparent; }
.sidebar.is-collapsed .auth-card :deep(.auth-badge) { width: 32px; justify-content: center; padding: 0; }
.main-content.is-sidebar-collapsed { margin-left: 84px; }
.mobile-header, .mobile-nav { display: none; }
.page-enter-active, .page-leave-active { transition: opacity .16s ease, transform .16s ease; }
.page-enter-from { opacity: 0; transform: translateY(5px); }
.page-leave-to { opacity: 0; transform: translateY(-3px); }

@media (min-width: 1200px) {
  .main-content { padding: 30px clamp(24px, 2.2vw, 40px) 42px; }
  .main-content.is-video-workspace { height: 100dvh; min-height: 0; overflow: hidden; }
}

@media (min-width: 768px) and (max-width: 1199px) {
  .sidebar-toggle { display: none; }
  .sidebar { width: 84px; padding-inline: 12px; }
  .brand { justify-content: center; padding-inline: 0; }
  .brand-copy, .desktop-nav button > span, .settings-subnav, .settings-parent .expand-indicator, .diagnostics-link span, .auth-card small, .auth-card :deep(.auth-badge span), .admin-session span { display: none; }
  .admin-session { display: flex; justify-content: center; padding-inline: 0; }.admin-session > .el-icon { display: none; }
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
    position: fixed; inset: auto 0 0; z-index: 40; display: grid; grid-template-columns: repeat(5, 1fr); padding: 7px 8px max(7px, env(safe-area-inset-bottom));
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
