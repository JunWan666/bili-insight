<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Clock, Delete, Link, Refresh, VideoPlay } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import { useVideosStore } from '@/stores/videos'
import type { RecentVideo } from '@/types/api'
import { formatDate, formatDuration } from '@/utils/format'

const router = useRouter()
const videos = useVideosStore()
const loading = ref(true)
const deletingId = ref<string | null>(null)

async function loadRecent(): Promise<void> {
  loading.value = true
  await videos.loadRecent(24)
  loading.value = false
}

async function removeRecent(video: RecentVideo): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `从最近解析中删除“${video.title}”？仅无关联任务和分析记录的视频可以删除。`,
      '删除解析记录',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消', customClass: 'compact-delete-confirm' },
    )
    deletingId.value = video.id
    await videos.removeRecent(video.id)
    ElMessage.success('最近解析记录已删除')
  } catch (reason) {
    if (reason !== 'cancel' && reason !== 'close') ElMessage.error(reason instanceof Error ? reason.message : '删除解析记录失败')
  } finally {
    deletingId.value = null
  }
}

onMounted(() => {
  void loadRecent()
})
</script>

<template>
  <div class="recent-view">
    <PageHeader
      eyebrow="RECENT MEDIA"
      title="最近解析"
      description="继续查看此前解析的视频、分 P 与可用媒体规格。"
    >
      <template #actions>
        <el-button :icon="Refresh" :loading="loading" @click="loadRecent">刷新</el-button>
        <el-button type="primary" :icon="VideoPlay" @click="router.push('/')">新建解析</el-button>
      </template>
    </PageHeader>

    <div v-if="loading" class="recent-skeleton" aria-label="正在加载最近解析">
      <el-skeleton v-for="index in 6" :key="index" animated>
        <template #template><el-skeleton-item variant="rect" class="skeleton-item" /></template>
      </el-skeleton>
    </div>

    <el-empty v-else-if="!videos.recent.length" description="还没有最近解析记录">
      <el-button type="primary" @click="router.push('/')">开始解析</el-button>
    </el-empty>

    <div v-else class="recent-grid">
      <article v-for="video in videos.recent" :key="video.id" class="recent-card surface-card" data-testid="recent-card">
        <RouterLink :to="`/videos/${video.id}`" class="recent-cover">
          <img :src="video.coverUrl" :alt="`${video.title} 封面`" loading="lazy" referrerpolicy="no-referrer" />
          <span>{{ formatDuration(video.duration) }}</span>
        </RouterLink>
        <div class="recent-content">
          <RouterLink :to="`/videos/${video.id}`" class="recent-title">{{ video.title }}</RouterLink>
          <p>{{ video.ownerName }}</p>
          <small><el-icon><Clock /></el-icon>{{ formatDate(video.parsedAt) }}</small>
          <div class="recent-actions">
            <a :href="video.normalizedUrl" target="_blank" rel="noopener noreferrer"><el-icon><Link /></el-icon>官方源视频</a>
            <el-tooltip content="删除解析记录" placement="top">
              <el-button text type="danger" :icon="Delete" :loading="deletingId === video.id" aria-label="删除解析记录" @click="removeRecent(video)" />
            </el-tooltip>
          </div>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
.recent-view { width: 100%; }
.recent-grid, .recent-skeleton { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.recent-card { display: grid; grid-template-columns: minmax(140px, 42%) minmax(0, 1fr); min-height: 154px; overflow: hidden; }
.recent-cover { position: relative; min-width: 0; overflow: hidden; background: var(--surface-muted); }
.recent-cover img { width: 100%; height: 100%; object-fit: cover; transition: transform .18s ease; }
.recent-cover:hover img { transform: scale(1.025); }
.recent-cover span { position: absolute; right: 8px; bottom: 8px; padding: 3px 6px; border-radius: 5px; background: rgba(18, 20, 25, .8); color: white; font-size: 10px; }
.recent-content { display: flex; flex-direction: column; min-width: 0; padding: 13px; }
.recent-title { display: -webkit-box; overflow: hidden; color: var(--text-primary); font-size: 14px; font-weight: 750; line-height: 1.45; text-decoration: none; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.recent-title:hover { color: var(--brand); }
.recent-content p { margin: 9px 0 5px; overflow: hidden; color: var(--text-secondary); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.recent-content small { display: flex; align-items: center; gap: 5px; color: var(--text-tertiary); font-size: 11px; }
.recent-actions { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: auto; padding-top: 8px; }
.recent-actions a { display: inline-flex; align-items: center; gap: 4px; color: var(--brand); font-size: 11px; font-weight: 700; text-decoration: none; }
.recent-actions .el-button { min-width: 40px; min-height: 40px; }
.skeleton-item { width: 100%; height: 154px; border-radius: var(--radius-lg); }

@media (max-width: 1399px) {
  .recent-grid, .recent-skeleton { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 767px) {
  .recent-grid, .recent-skeleton { grid-template-columns: 1fr; gap: 10px; }
  .recent-card { grid-template-columns: 132px minmax(0, 1fr); min-height: 138px; }
  .recent-content { padding: 11px; }
  .recent-content p { margin-top: 6px; }
  .recent-actions .el-button { min-width: 44px; min-height: 44px; }
  .skeleton-item { height: 138px; }
}

@media (max-width: 374px) {
  .recent-card { grid-template-columns: 116px minmax(0, 1fr); }
}
</style>
