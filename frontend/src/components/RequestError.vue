<script setup lang="ts">
import { WarningFilled } from '@element-plus/icons-vue'
import type { ApiError } from '@/api/errors'

defineProps<{ error: ApiError; title?: string }>()
defineEmits<{ retry: [] }>()
</script>

<template>
  <div class="request-error" role="alert">
    <el-icon><WarningFilled /></el-icon>
    <div class="error-copy">
      <strong>{{ title || error.message }}</strong>
      <span>{{ title ? error.message : error.action }}</span>
      <small v-if="error.requestId">请求编号：{{ error.requestId }}</small>
    </div>
    <el-button v-if="$attrs.onRetry" class="retry" @click="$emit('retry')">重试</el-button>
  </div>
</template>

<style scoped>
.request-error {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 16px;
  border: 1px solid #f1b9ad;
  border-radius: var(--radius-md);
  background: #fff4f1;
  color: #8c2b1e;
}
.request-error > .el-icon { flex: 0 0 auto; margin-top: 2px; font-size: 20px; }
.error-copy { display: grid; gap: 4px; min-width: 0; line-height: 1.45; }
.error-copy span { color: #9f5145; }
.error-copy small { color: #af746b; overflow-wrap: anywhere; }
.retry { margin-left: auto; }
@media (max-width: 480px) { .request-error { flex-wrap: wrap; } .retry { width: 100%; margin-left: 32px; } }
</style>
