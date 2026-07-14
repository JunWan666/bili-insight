const phaseLabels: Record<string, string> = {
  queued: '等待调度',
  preparing: '准备任务',
  recovering: '恢复任务',
  pausing: '正在安全暂停',
  canceling: '正在取消任务',
  post_processing: '后处理与验证',
  refreshing_video: '刷新视频地址',
  refreshing_audio: '刷新音频地址',
  reusing_video: '复用视频分片',
  reusing_audio: '复用音频分片',
  downloading_video: '下载视频流',
  downloading_audio: '下载音频流',
  downloading_cover: '下载封面',
  downloading_subtitle: '下载字幕',
  downloading_danmaku: '下载弹幕',
  merging: '无损合并',
  transcoding: '兼容转码',
  verifying: '验证最终媒体',
  analyzing: '分析媒体',
  transcribing: '语音转写',
  recognizing: '识别画面文字',
  detecting_scenes: '切分镜头',
  summarizing: '生成摘要',
  cleaning: '清理临时文件',
  analysis_preparing: '准备分析',
  analysis_media_acquisition: '获取分析媒体',
  analysis_basic: '基础内容分析',
  analysis_media: '媒体技术分析',
  analysis_audio: '音频技术分析',
  analysis_subtitles: '获取公开字幕',
  analysis_asr: '语音转写',
  analysis_ocr: '画面文字识别',
  analysis_scenes: '镜头与关键帧分析',
  analysis_summary: '生成内容摘要',
  analysis_manifest: '生成分析清单',
  completed: '已完成',
  paused: '已暂停',
  canceled: '已取消',
  failed: '执行失败',
}

export function jobPhaseLabel(phase: string, fallback: string): string {
  const normalized = phase.trim().toLowerCase()
  if (phaseLabels[normalized]) return phaseLabels[normalized]
  if (normalized.startsWith('analysis_acquire_')) return '获取分析媒体'
  if (normalized.startsWith('analysis_')) return '执行内容分析'
  if (normalized.startsWith('refreshing_')) return '刷新媒体地址'
  if (normalized.startsWith('reusing_')) return '复用已下载分片'
  if (normalized.startsWith('downloading_')) return '下载媒体资源'
  return fallback
}
