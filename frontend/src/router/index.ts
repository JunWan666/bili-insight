import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { pinia } from '@/stores'
import { useAppAuthStore } from '@/stores/appAuth'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'app-login',
    component: () => import('@/views/AppLoginView.vue'),
    meta: { title: '管理员登录', public: true },
  },
  {
    path: '/',
    name: 'home',
    component: () => import('@/views/HomeView.vue'),
    meta: { title: '解析视频' },
  },
  {
    path: '/videos/:videoId',
    name: 'video-detail',
    component: () => import('@/views/VideoDetailView.vue'),
    meta: { title: '视频详情' },
  },
  {
    path: '/recent',
    name: 'recent',
    component: () => import('@/views/RecentView.vue'),
    meta: { title: '最近解析' },
  },
  {
    path: '/jobs',
    name: 'jobs',
    component: () => import('@/views/JobsView.vue'),
    meta: { title: '任务中心' },
  },
  {
    path: '/artifacts',
    name: 'artifacts',
    component: () => import('@/views/ArtifactsView.vue'),
    meta: { title: '产物与历史' },
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('@/views/SettingsView.vue'),
    meta: { title: '设置' },
  },
  {
    path: '/diagnostics',
    name: 'diagnostics',
    component: () => import('@/views/DiagnosticsView.vue'),
    meta: { title: '关于与诊断' },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFoundView.vue'),
    meta: { title: '页面不存在' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (route) => {
  const auth = useAppAuthStore(pinia)
  try {
    await auth.load()
  } catch {
    if (route.name === 'app-login') return true
    return { name: 'app-login' }
  }
  if (route.name === 'app-login') {
    return auth.authenticated ? { name: 'home' } : true
  }
  if (!auth.authenticated) {
    return {
      name: 'app-login',
      query: route.fullPath === '/' ? undefined : { returnTo: route.fullPath },
    }
  }
  return true
})

router.afterEach((route) => {
  document.title = `${String(route.meta.title ?? '视频工作台')} · Bili Insight`
})

export default router
