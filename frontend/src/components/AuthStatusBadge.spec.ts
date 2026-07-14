import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import AuthStatusBadge from './AuthStatusBadge.vue'
import type { AuthStatus } from '@/types/api'

function status(overrides: Partial<AuthStatus>): AuthStatus {
  return {
    status: 'anonymous', isAuthenticated: false, isPremium: false, maskedAccountName: null,
    membershipType: null, cookieExpiresAt: null, lastValidatedAt: null, remembered: false, message: null,
    ...overrides,
  }
}

describe('AuthStatusBadge', () => {
  it('distinguishes anonymous and premium state', async () => {
    const wrapper = mount(AuthStatusBadge, { props: { status: status({}) } })
    expect(wrapper.text()).toContain('匿名模式')
    await wrapper.setProps({ status: status({ status: 'premium', isAuthenticated: true, isPremium: true }) })
    expect(wrapper.text()).toContain('大会员有效')
    expect(wrapper.classes()).toContain('is-premium')
  })

  it('announces validation state', () => {
    const wrapper = mount(AuthStatusBadge, { props: { status: status({ status: 'validating' }) } })
    expect(wrapper.attributes('role')).toBe('status')
    expect(wrapper.text()).toContain('校验中')
  })
})
