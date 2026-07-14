import { describe, expect, it } from 'vitest'
import { formatBitrate, formatBytes, formatDuration, formatNumber } from './format'

describe('display formatters', () => {
  it('formats media durations and sizes', () => {
    expect(formatDuration(218)).toBe('03:38')
    expect(formatDuration(3723)).toBe('01:02:03')
    expect(formatBytes(1024 ** 2 * 12.5)).toBe('12.5 MB')
  })

  it('formats bitrate while preserving the unit', () => {
    expect(formatBitrate(3_550_000)).toBe('3.55 Mbps')
    expect(formatBitrate(188_000)).toBe('188 kbps')
  })

  it('renders missing values as 暂无 instead of a fake zero', () => {
    expect(formatBytes(null)).toBe('暂无')
    expect(formatNumber(null)).toBe('暂无')
  })
})
