const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
})

const compactFormatter = new Intl.NumberFormat('zh-CN', {
  notation: 'compact',
  maximumFractionDigits: 1,
})

export function formatDate(value: string | null | undefined): string {
  if (!value) return '暂无'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '暂无' : dateFormatter.format(date)
}

export function formatDuration(totalSeconds: number | null | undefined): string {
  if (totalSeconds == null || !Number.isFinite(totalSeconds) || totalSeconds < 0) return '暂无'
  const seconds = Math.floor(totalSeconds)
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remainder = seconds % 60
  return hours > 0
    ? [hours, minutes, remainder].map((unit) => String(unit).padStart(2, '0')).join(':')
    : [minutes, remainder].map((unit) => String(unit).padStart(2, '0')).join(':')
}

export function formatBytes(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value) || value < 0) return '暂无'
  if (value === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const order = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  const scaled = value / 1024 ** order
  return `${scaled.toFixed(scaled >= 100 || order === 0 ? 0 : scaled >= 10 ? 1 : 2)} ${units[order]}`
}

export function formatBitrate(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value) || value < 0) return '暂无'
  return value >= 1_000_000 ? `${(value / 1_000_000).toFixed(2)} Mbps` : `${Math.round(value / 1000)} kbps`
}

export function formatNumber(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '暂无' : compactFormatter.format(value)
}

export function formatEta(value: number | null | undefined): string {
  if (value == null || value < 0 || !Number.isFinite(value)) return '计算中'
  if (value < 60) return `约 ${Math.ceil(value)} 秒`
  if (value < 3600) return `约 ${Math.ceil(value / 60)} 分钟`
  return `约 ${(value / 3600).toFixed(1)} 小时`
}
