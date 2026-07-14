const videoPath = /^\/videos\/[A-Za-z0-9-]{1,64}$/
const controlCharacters = /[\u0000-\u001F\u007F]/

export function safeVideoReturnPath(value: unknown): string | null {
  if (typeof value !== 'string' || value.length === 0 || value.length > 2048) return null
  if (value.trim() !== value || !value.startsWith('/') || value.startsWith('//')) return null
  if (value.includes('\\') || controlCharacters.test(value)) return null
  try {
    const decoded = decodeURIComponent(value)
    if (decoded.startsWith('//') || decoded.includes('\\') || controlCharacters.test(decoded)) return null
    const target = new URL(value, 'https://local.invalid')
    if (target.origin !== 'https://local.invalid' || !videoPath.test(target.pathname)) return null
    return `${target.pathname}${target.search}${target.hash}`
  } catch {
    return null
  }
}
