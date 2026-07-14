import { describe, expect, it } from 'vitest'
import { codecFamily, copyCompatibilityIssue, isBestCompatibilitySource } from './mediaCompatibility'
import type { MediaStream } from '@/types/api'

function stream(kind: 'video' | 'audio', codec: string): MediaStream {
  return {
    id: `${kind}-${codec}`,
    partId: 'part-1',
    kind,
    qualityCode: '1',
    qualityLabel: 'test',
    codec,
    container: 'm4s',
    width: kind === 'video' ? 1920 : null,
    height: kind === 'video' ? 1080 : null,
    fps: kind === 'video' ? 25 : null,
    bitrate: 128_000,
    hdrType: null,
    audioChannels: kind === 'audio' ? 2 : null,
    sampleRate: kind === 'audio' ? 48_000 : null,
    estimatedSize: 1_000,
    authRequired: false,
    premiumRequired: false,
    accessRequirement: 'none',
    verifiedAt: null,
    compatibleDevices: [],
    compatibilityNote: null,
  }
}

describe('media copy compatibility', () => {
  const h264 = stream('video', 'H.264/AVC')
  const aac = stream('audio', 'AAC')

  it('defines the best compatibility source as H.264 plus AAC', () => {
    expect(isBestCompatibilitySource(h264, aac)).toBe(true)
    expect(isBestCompatibilitySource(stream('video', 'AV1'), aac)).toBe(false)
    expect(isBestCompatibilitySource(h264, stream('audio', 'FLAC'))).toBe(false)
  })

  it.each(['FLAC', 'Dolby E-AC-3'])('blocks %s copy into MP4 and M4A while retaining MKV', (codec) => {
    const audio = stream('audio', codec)
    expect(copyCompatibilityIssue('mp4', h264, audio)).toContain(codec)
    expect(copyCompatibilityIssue('m4a', null, audio)).toContain(codec)
    expect(copyCompatibilityIssue('mkv', h264, audio)).toBeNull()
  })

  it('normalizes provider codec aliases and forces encoded audio formats to transcode', () => {
    expect(codecFamily('Dolby Digital Plus / E-AC-3')).toBe('eac3')
    expect(codecFamily('avc1.640028')).toBe('h264')
    expect(copyCompatibilityIssue('mp3', null, aac)).toContain('必须转码')
    expect(copyCompatibilityIssue('flac', null, aac)).toContain('必须转码')
  })
})
