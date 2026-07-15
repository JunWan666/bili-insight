const videoPath = /^\/videos\/[A-Za-z0-9-]{1,64}$/
const appPath = /^\/(?:|videos\/[A-Za-z0-9-]{1,64}|jobs|artifacts|settings|diagnostics)$/
const controlCharacters = /[\u0000-\u001F\u007F]/

function localTarget(value: unknown): URL | null {
  if (typeof value !== 'string' || value.length === 0 || value.length > 2048) return null
  if (value.trim() !== value || !value.startsWith('/') || value.startsWith('//')) return null
  if (value.includes('\\') || controlCharacters.test(value)) return null
  try {
    const decoded = decodeURIComponent(value)
    if (decoded.startsWith('//') || decoded.includes('\\') || controlCharacters.test(decoded)) return null
    const target = new URL(value, 'https://local.invalid')
    return target.origin === 'https://local.invalid' ? target : null
  } catch {
    return null
  }
}

export function safeReturnPath(value: unknown): string {
  const target = localTarget(value)
  if (!target || !appPath.test(target.pathname)) return '/'
  return `${target.pathname}${target.search}${target.hash}`
}

export function safeVideoReturnPath(value: unknown): string | null {
  const target = localTarget(value)
  if (!target || !videoPath.test(target.pathname)) return null
  return `${target.pathname}${target.search}${target.hash}`
}
