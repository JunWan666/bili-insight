import { describe, expect, it } from 'vitest'
import { normalizeVideoInput, VideoInputError } from './videoUrl'

describe('normalizeVideoInput', () => {
  it('converts a BV id into the canonical video URL', () => {
    expect(normalizeVideoInput(' BV1FYT5zkE1q ')).toEqual({
      url: 'https://www.bilibili.com/video/BV1FYT5zkE1q',
      partNumber: null,
    })
  })

  it('accepts an AV id', () => {
    expect(normalizeVideoInput('av170001').url).toBe('https://www.bilibili.com/video/av170001')
  })

  it('removes tracking parameters and preserves a valid part number', () => {
    const result = normalizeVideoInput('https://www.bilibili.com/video/BV1FYT5zkE1q/?spm_id_from=333.1&vd_source=secret&p=2')
    expect(result.url).toBe('https://www.bilibili.com/video/BV1FYT5zkE1q?p=2')
    expect(result.partNumber).toBe(2)
  })

  it.each([
    'http://www.bilibili.com/video/BV1FYT5zkE1q',
    'https://example.com/video/BV1FYT5zkE1q',
    'file:///etc/passwd',
    'https://www.bilibili.com/bangumi/play/ep1',
  ])('rejects unsafe or unsupported input: %s', (value) => {
    expect(() => normalizeVideoInput(value)).toThrow(VideoInputError)
  })

  it('rejects an empty value with an actionable message', () => {
    expect(() => normalizeVideoInput('')).toThrow('请输入 Bilibili 视频链接')
  })
})
