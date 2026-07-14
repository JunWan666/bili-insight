import type { MediaStream, OutputContainer } from '@/types/api'

export type CodecFamily =
  | 'h264'
  | 'hevc'
  | 'av1'
  | 'aac'
  | 'flac'
  | 'eac3'
  | 'ac3'
  | 'opus'
  | 'mp3'
  | 'vorbis'
  | 'vp8'
  | 'vp9'
  | 'mpeg4'
  | 'unknown'

export function codecFamily(codec: string): CodecFamily {
  const value = codec.toLowerCase().replaceAll('_', '-').trim()
  const aliases: Array<[CodecFamily, string[]]> = [
    ['h264', ['h.264', 'h264', 'avc1', 'avc']],
    ['hevc', ['h.265', 'h265', 'hevc', 'hev1', 'hvc1']],
    ['av1', ['av1', 'av01']],
    ['aac', ['aac', 'mp4a']],
    ['flac', ['flac']],
    ['eac3', ['e-ac-3', 'eac3', 'ec-3', 'dolby digital plus']],
    ['ac3', ['ac-3', 'ac3', 'dolby digital']],
    ['opus', ['opus']],
    ['mp3', ['mp3', 'mpeg layer 3']],
    ['vorbis', ['vorbis']],
    ['vp9', ['vp9', 'vp09']],
    ['vp8', ['vp8', 'vp08']],
    ['mpeg4', ['mpeg-4 visual', 'mpeg4']],
  ]
  return aliases.find(([, candidates]) => candidates.some((candidate) => value.includes(candidate)))?.[0] ?? 'unknown'
}

const copyVideoCodecs: Partial<Record<OutputContainer, CodecFamily[]>> = {
  mp4: ['h264', 'hevc', 'av1'],
  mkv: ['h264', 'hevc', 'av1', 'vp8', 'vp9', 'mpeg4'],
}

const copyAudioCodecs: Partial<Record<OutputContainer, CodecFamily[]>> = {
  mp4: ['aac'],
  m4a: ['aac'],
  mkv: ['aac', 'flac', 'eac3', 'ac3', 'opus', 'mp3', 'vorbis'],
}

export function copyCompatibilityIssue(
  container: OutputContainer,
  video: MediaStream | null,
  audio: MediaStream | null,
): string | null {
  if (container === 'mp3' || container === 'flac') return `${container.toUpperCase()} 输出必须转码`
  if (video && !(copyVideoCodecs[container] ?? []).includes(codecFamily(video.codec))) {
    return `${video.codec} 视频不能安全无损封装到 ${container.toUpperCase()}`
  }
  if (audio && !(copyAudioCodecs[container] ?? []).includes(codecFamily(audio.codec))) {
    return `${audio.codec} 音频不能安全无损封装到 ${container.toUpperCase()}`
  }
  return null
}

export function isBestCompatibilitySource(
  video: MediaStream | null,
  audio: MediaStream | null,
): boolean {
  return video !== null
    && codecFamily(video.codec) === 'h264'
    && (audio === null || codecFamily(audio.codec) === 'aac')
}
