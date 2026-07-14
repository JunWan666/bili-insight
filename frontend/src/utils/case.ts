const snakePattern = /_([a-z])/g
const camelPattern = /[A-Z]/g

export function toCamelCase(value: string): string {
  return value.replace(snakePattern, (_, letter: string) => letter.toUpperCase())
}

export function toSnakeCase(value: string): string {
  return value.replace(camelPattern, (letter) => `_${letter.toLowerCase()}`)
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Object.prototype.toString.call(value) === '[object Object]'
}

export function camelize<T>(value: unknown): T {
  if (Array.isArray(value)) {
    return value.map((item) => camelize(item)) as T
  }
  if (isPlainObject(value)) {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [toCamelCase(key), camelize(item)]),
    ) as T
  }
  return value as T
}

export function decamelize<T>(value: unknown): T {
  if (Array.isArray(value)) {
    return value.map((item) => decamelize(item)) as T
  }
  if (isPlainObject(value)) {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [toSnakeCase(key), decamelize(item)]),
    ) as T
  }
  return value as T
}
