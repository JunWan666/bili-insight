<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Clock, Delete, Link, Refresh, User, VideoPlay } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import { useVideosStore } from '@/stores/videos'
import type { RecentVideo } from '@/types/api'
import { formatDate, formatDuration } from '@/utils/format'

const router = useRouter()
const videos = useVideosStore()
const loading = ref(true)
const deletingId = ref<string | null>(null)

function sourceLabel(video: RecentVideo): string {
  return video.normalizedUrl.includes('/bangumi/') ? '番剧' : '投稿'
}

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
          <p><el-icon><User /></el-icon>{{ video.ownerName }}</p>
          <div class="recent-meta">
            <span>{{ sourceLabel(video) }}</span>
            <span>{{ video.bvid }}</span>
          </div>
          <div class="recent-actions">
            <small><el-icon><Clock /></el-icon>{{ formatDate(video.parsedAt) }}</small>
            <div class="recent-action-icons">
              <el-tooltip content="打开官方源视频" placement="top">
                <a class="source-link" :href="video.normalizedUrl" target="_blank" rel="noopener noreferrer" aria-label="打开官方源视频"><el-icon><Link /></el-icon></a>
              </el-tooltip>
              <el-tooltip content="删除解析记录" placement="top">
                <el-button text type="danger" :icon="Delete" :loading="deletingId === video.id" aria-label="删除解析记录" @click="removeRecent(video)" />
              </el-tooltip>
            </div>
          </div>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
.recent-view { width: 100%; }
.recent-grid, .recent-skeleton { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 14px; }
.recent-card { display: flex; min-width: 0; min-height: 238px; flex-direction: column; overflow: hidden; }
.recent-cover { position: relative; min-width: 0; aspect-ratio: 16 / 9; overflow: hidden; background: var(--surface-muted); }
.recent-cover img { width: 100%; height: 100%; object-fit: cover; transition: transform .18s ease; }
.recent-cover:hover img { transform: scale(1.025); }
.recent-cover span { position: absolute; right: 8px; bottom: 8px; padding: 3px 6px; border-radius: 5px; background: rgba(18, 20, 25, .8); color: white; font-size: 10px; }
.recent-content { display: flex; flex: 1; flex-direction: column; min-width: 0; padding: 11px 12px; }
.recent-title { display: -webkit-box; overflow: hidden; color: var(--text-primary); font-size: 14px; font-weight: 750; line-height: 1.42; text-decoration: none; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.recent-title:hover { color: var(--brand); }
.recent-content p { display: flex; align-items: center; gap: 4px; margin: 5px 0 4px; overflow: hidden; color: var(--text-secondary); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }.recent-content p .el-icon { flex: 0 0 auto; }
.recent-meta { display: flex; gap: 5px; margin-bottom: 4px; overflow: hidden; }.recent-meta span { min-width: 0; padding: 2px 5px; border-radius: 4px; background: var(--surface-muted); color: var(--text-tertiary); font-size: 9px; line-height: 1.2; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.recent-meta span:last-child { flex: 1; }
.recent-content small { display: flex; align-items: center; gap: 4px; color: var(--text-tertiary); font-size: 10px; }
.recent-actions { display: flex; align-items: center; justify-content: space-between; gap: 4px; margin-top: auto; }
.recent-action-icons { display: flex; align-items: center; flex: 0 0 auto; gap: 1px; }
.source-link { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 8px; color: var(--brand); text-decoration: none; }.source-link:hover { background: var(--brand-soft); }
.recent-actions .el-button { min-width: 32px; min-height: 32px; }
.skeleton-item { width: 100%; height: 238px; border-radius: var(--radius-lg); }

@media (max-width: 1399px) {
  .recent-grid, .recent-skeleton { grid-template-columns: repeat(4, minmax(0, 1fr)); }
}

@media (max-width: 1199px) {
  .recent-grid, .recent-skeleton { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}

@media (max-width: 900px) {
  .recent-grid, .recent-skeleton { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 767px) {
  .recent-grid, .recent-skeleton { grid-template-columns: 1fr; gap: 10px; }
  .recent-card { display: grid; grid-template-columns: 120px minmax(0, 1fr); min-height: 132px; }
  .recent-cover { aspect-ratio: auto; }
  .recent-content { padding: 11px; }
  .recent-content p { margin-top: 6px; }
  .source-link { width: 44px; height: 44px; }
  .recent-actions .el-button { min-width: 44px; min-height: 44px; }
  .skeleton-item { height: 132px; }
}

@media (max-width: 374px) {
  .recent-card { grid-template-columns: 116px minmax(0, 1fr); }
}
</style>
