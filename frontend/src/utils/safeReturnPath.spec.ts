import { describe, expect, it } from 'vitest'
import { safeReturnPath, safeVideoReturnPath } from './safeReturnPath'

describe('safeReturnPath', () => {
  it.each(['/', '/jobs', '/artifacts?page=2', '/settings/account', '/diagnostics'])(
    'allows a known local application route %s',
    (value) => expect(safeReturnPath(value)).toBe(value),
  )

  it.each(['https://evil.example/jobs', '//evil.example/jobs', '/\\evil.example', '/unknown', '/jobs%0A'])(
    'falls back to the home page for an unsafe route %s',
    (value) => expect(safeReturnPath(value)).toBe('/'),
  )
})

describe('safeVideoReturnPath', () => {
  it('allows only local video detail routes and preserves their query', () => {
    expect(safeVideoReturnPath('/videos/123e4567-e89b-42d3-a456-426614174000?part=part-2')).toBe(
      '/videos/123e4567-e89b-42d3-a456-426614174000?part=part-2',
    )
  })

  it.each([
    'https://evil.example/videos/id',
    '//evil.example/videos/id',
    '/\\evil.example/videos/id',
    '/settings',
    '/videos/id%0Aevil',
    '/videos/../../settings',
  ])('rejects unsafe or out-of-scope return target %s', (value) => {
    expect(safeVideoReturnPath(value)).toBeNull()
  })
})
