import { config } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { vi } from 'vitest'

config.global.plugins = [ElementPlus]

class TestResizeObserver implements ResizeObserver {
  readonly observationTargets = new Set<Element>()
  disconnect(): void { this.observationTargets.clear() }
  observe(target: Element): void { this.observationTargets.add(target) }
  unobserve(target: Element): void { this.observationTargets.delete(target) }
}

Object.defineProperty(window, 'ResizeObserver', { writable: true, value: TestResizeObserver })
Object.defineProperty(globalThis, 'ResizeObserver', { writable: true, value: TestResizeObserver })
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

Object.defineProperty(URL, 'createObjectURL', { writable: true, value: vi.fn(() => 'blob:test') })
Object.defineProperty(URL, 'revokeObjectURL', { writable: true, value: vi.fn() })
