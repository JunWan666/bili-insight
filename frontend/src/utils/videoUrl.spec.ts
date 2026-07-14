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
    ['ss28747', 'https://www.bilibili.com/bangumi/play/ss28747'],
    ['ep733316', 'https://www.bilibili.com/bangumi/play/ep733316'],
    ['https://www.bilibili.com/bangumi/play/ss28747?from_spmid=666.5.mylist.0', 'https://www.bilibili.com/bangumi/play/ss28747'],
  ])('normalizes a bangumi input: %s', (input, expected) => {
    expect(normalizeVideoInput(input)).toEqual({ url: expected, partNumber: null })
  })

  it.each([
    'http://www.bilibili.com/video/BV1FYT5zkE1q',
    'https://example.com/video/BV1FYT5zkE1q',
    'file:///etc/passwd',
    'https://www.bilibili.com/bangumi/media/md1',
  ])('rejects unsafe or unsupported input: %s', (value) => {
    expect(() => normalizeVideoInput(value)).toThrow(VideoInputError)
  })

  it('rejects an empty value with an actionable message', () => {
    expect(() => normalizeVideoInput('')).toThrow('请输入 Bilibili 视频链接')
  })
})
