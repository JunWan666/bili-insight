import { describe, expect, it } from 'vitest'
import { endOfLocalDayIso } from './dateRange'

describe('endOfLocalDayIso', () => {
  it('includes the complete selected local calendar day', () => {
    const source = new Date(2026, 6, 14, 8, 30, 0, 0)
    const result = new Date(endOfLocalDayIso(source))

    expect(result.getFullYear()).toBe(2026)
    expect(result.getMonth()).toBe(6)
    expect(result.getDate()).toBe(14)
    expect(result.getHours()).toBe(23)
    expect(result.getMinutes()).toBe(59)
    expect(result.getSeconds()).toBe(59)
    expect(result.getMilliseconds()).toBe(999)
    expect(source.getHours()).toBe(8)
  })
})
