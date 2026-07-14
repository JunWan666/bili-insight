import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import ArtifactsView from './ArtifactsView.vue'

const list = vi.hoisted(() => vi.fn())
const storage = vi.hoisted(() => vi.fn())

vi.mock('@/api', () => ({
  artifactApi: {
    list,
    storage,
    get: vi.fn(),
    content: vi.fn(),
    contentUrl: vi.fn(),
    remove: vi.fn(),
  },
}))
vi.mock('vue-router', () => ({ useRoute: () => ({ query: {} }) }))

describe('ArtifactsView date filters', () => {
  it('sends the selected end date as the end of the local day', async () => {
    list.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 })
    storage.mockResolvedValue({ artifactBytes: 0, freeBytes: 100, totalBytes: 100 })
    const wrapper = mount(ArtifactsView, {
      global: {
        stubs: { RouterLink: true },
        mocks: { $router: { push: vi.fn(), replace: vi.fn() } },
      },
    })
    await flushPromises()
    const picker = wrapper.findComponent({ name: 'ElDatePicker' })
    const start = new Date(2026, 6, 1, 0, 0, 0, 0)
    const end = new Date(2026, 6, 14, 0, 0, 0, 0)

    picker.vm.$emit('update:modelValue', [start, end])
    await wrapper.vm.$nextTick()
    const onChange = picker.vm.$.vnode.props?.onChange
    expect(onChange).toBeTypeOf('function')
    ;(onChange as (value: [Date, Date]) => void)([start, end])
    await flushPromises()

    const filters = list.mock.calls.at(-1)?.[0]
    expect(filters.from).toBe(start.toISOString())
    const requestedEnd = new Date(filters.to)
    expect(requestedEnd.getFullYear()).toBe(2026)
    expect(requestedEnd.getMonth()).toBe(6)
    expect(requestedEnd.getDate()).toBe(14)
    expect(requestedEnd.getHours()).toBe(23)
    expect(requestedEnd.getMinutes()).toBe(59)
    expect(requestedEnd.getSeconds()).toBe(59)
    expect(requestedEnd.getMilliseconds()).toBe(999)
  })
})
