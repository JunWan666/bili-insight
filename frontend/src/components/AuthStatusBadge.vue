<script setup lang="ts">
import { computed } from 'vue'
import { CircleCheck, Connection, Loading, Lock, Warning } from '@element-plus/icons-vue'
import type { AuthStatus } from '@/types/api'

const props = defineProps<{
  status: AuthStatus | null
  loading?: boolean
  compact?: boolean
}>()

const view = computed(() => {
  if (props.loading && !props.status) return { text: '检查登录态', className: 'is-loading', icon: Loading }
  switch (props.status?.status) {
    case 'premium':
      return { text: '大会员有效', className: 'is-premium', icon: CircleCheck }
    case 'authenticated':
      return { text: '已登录', className: 'is-authenticated', icon: CircleCheck }
    case 'validating':
      return { text: '校验中', className: 'is-loading', icon: Loading }
    case 'expired':
      return { text: '登录已失效', className: 'is-warning', icon: Warning }
    case 'error':
      return { text: '验证异常', className: 'is-warning', icon: Connection }
    default:
      return { text: '匿名模式', className: 'is-anonymous', icon: Lock }
  }
})
</script>

<template>
  <span class="auth-badge" :class="[view.className, { compact }]" role="status" data-testid="auth-status">
    <el-icon :class="{ spinning: view.className === 'is-loading' }"><component :is="view.icon" /></el-icon>
    <span>{{ view.text }}</span>
  </span>
</template>

<style scoped>
.auth-badge {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 32px;
  padding: 5px 10px;
  border: 1px solid var(--line-soft);
  border-radius: 999px;
  background: var(--surface-muted);
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 650;
  white-space: nowrap;
}

.is-premium { color: #7b4b02; background: #fff7df; border-color: #f2d18b; }
.is-authenticated { color: #126344; background: #eaf9f2; border-color: #afe3cf; }
.is-warning { color: #9a4b11; background: #fff5e9; border-color: #f2c497; }
.is-loading { color: var(--brand); background: var(--brand-soft); border-color: #b8c7f8; }
.compact { min-height: 28px; padding: 3px 8px; font-size: 12px; }

.spinning { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
