<script setup lang="ts">
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { Loading } from '@element-plus/icons-vue'
import { useRoute } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'

const route = useRoute()
</script>

<template>
  <el-config-provider :locale="zhCn">
    <RouterView v-if="route.name === 'app-login'" />
    <AppShell v-else-if="route.name" />
    <main v-else class="route-loading" aria-label="正在验证应用会话">
      <el-icon class="is-loading"><Loading /></el-icon>
    </main>
  </el-config-provider>
</template>

<style scoped>
.route-loading { display: grid; min-height: 100dvh; place-items: center; color: var(--brand); }
.route-loading .el-icon { font-size: 28px; }
</style>
