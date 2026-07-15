import { expect, test } from './fixtures/api'

test('首次启动可创建唯一管理员并进入应用', async ({ page, testApi }) => {
  testApi.setAppInitialized(false)
  testApi.setAppAuthenticated(false)
  await page.goto('/')

  await expect(page).toHaveURL(/\/login/)
  await expect(page.getByRole('heading', { name: '创建管理员账号' })).toBeVisible()
  await page.getByLabel('用户名').fill('mobile-admin')
  await page.getByLabel('密码', { exact: true }).fill('mobile-admin-password-2026')
  await page.getByLabel('确认密码').fill('mobile-admin-password-2026')
  await page.getByRole('button', { name: '初始化并进入' }).click()

  await expect(page).toHaveURL(/\/$/)
  expect(testApi.state.appInitialized).toBe(true)
  expect(testApi.state.appAuthenticated).toBe(true)
  expect(testApi.state.appUsername).toBe('mobile-admin')
})

test('未登录访问受保护页面会返回登录页并在登录后继续', async ({ page, testApi }) => {
  testApi.setAppAuthenticated(false)
  await page.goto('/jobs')

  await expect(page).toHaveURL(/\/login\?returnTo=/)
  await expect(page.getByRole('heading', { name: '管理员登录' })).toBeVisible()
  await page.getByLabel('用户名').fill('e2e-admin')
  await page.getByLabel('密码').fill('e2e-admin-password-2026')
  await page.getByRole('button', { name: '登录' }).click()

  await expect(page).toHaveURL(/\/jobs$/)
  await expect(page.getByTestId('job-card')).toHaveCount(2)
})
