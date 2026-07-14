<script setup lang="ts">
import { Compass } from '@element-plus/icons-vue'
</script>

<template>
  <div class="not-found surface-card">
    <span>404</span>
    <h1>这里没有可查看的页面</h1>
    <p>链接可能已失效，或页面地址输入有误。</p>
    <el-button type="primary" size="large" :icon="Compass" @click="$router.replace('/')">返回解析首页</el-button>
  </div>
</template>

<style scoped>
.not-found { display: grid; place-items: center; max-width: 620px; min-height: 420px; margin: 8vh auto 0; padding: 40px; text-align: center; }.not-found > span { color: var(--brand); font-size: clamp(72px, 13vw, 130px); font-weight: 850; letter-spacing: -.08em; line-height: 1; opacity: .18; }.not-found h1 { margin: -15px 0 0; font-size: 25px; }.not-found p { margin: 10px 0 24px; color: var(--text-secondary); }
</style>
