import type { Locator, Page } from '@playwright/test'
import { expect, test } from './fixtures/api'

interface PageContract {
  path: string
  heading: string
}

const pageContracts: PageContract[] = [
  { path: '/', heading: '粘贴 Bilibili 视频链接' },
  { path: '/videos/video-e2e', heading: 'E2E 测试专用：响应式视频解析样本' },
  { path: '/jobs', heading: '任务中心' },
  { path: '/artifacts', heading: '产物与历史' },
  { path: '/settings', heading: '设置' },
  { path: '/diagnostics', heading: '关于与诊断' },
]

async function assertNoPageOverflow(page: Page, path: string): Promise<void> {
  const result = await page.evaluate(() => {
    const viewportWidth = document.documentElement.clientWidth
    const candidates = Array.from(document.querySelectorAll<HTMLElement>('body *'))
      .filter((element) => {
        const style = getComputedStyle(element)
        if (style.display === 'none' || style.visibility === 'hidden') return false
        const rect = element.getBoundingClientRect()
        return rect.width > 0 && (rect.left < -1 || rect.right > viewportWidth + 1)
      })
      .slice(0, 8)
      .map((element) => {
        const rect = element.getBoundingClientRect()
        return {
          tag: element.tagName.toLowerCase(),
          className: element.className.toString().slice(0, 100),
          left: Math.round(rect.left),
          right: Math.round(rect.right),
        }
      })
    return {
      viewportWidth,
      htmlScrollWidth: document.documentElement.scrollWidth,
      bodyScrollWidth: document.body.scrollWidth,
      candidates,
    }
  })

  expect(
    Math.max(result.htmlScrollWidth, result.bodyScrollWidth),
    `${path} 不应产生页面级横向溢出；越界候选：${JSON.stringify(result.candidates)}`,
  ).toBeLessThanOrEqual(result.viewportWidth + 1)
}

async function assertTouchTarget(locator: Locator, name: string): Promise<void> {
  await expect(locator, `${name} 应在当前布局可见`).toBeVisible()
  const box = await locator.boundingBox()
  expect(box, `${name} 应具有可测量的触控区域`).not.toBeNull()
  expect(Math.round(box?.width ?? 0), `${name} 触控宽度`).toBeGreaterThanOrEqual(44)
  expect(Math.round(box?.height ?? 0), `${name} 触控高度`).toBeGreaterThanOrEqual(44)
}

test('全部核心页面在目标视口无页面级横向溢出', async ({ page, testApi }) => {
  testApi.setAuthenticated(true)
  for (const contract of pageContracts) {
    await page.goto(contract.path)
    await expect(page.getByRole('heading', { name: contract.heading }).first()).toBeVisible()
    await assertNoPageOverflow(page, contract.path)
  }

  await page.goto('/videos/video-e2e')
  await page.getByTestId('tab-technical-analysis').click()
  await expect(page.getByTestId('analysis-result-media')).toBeVisible()
  await assertNoPageOverflow(page, '/videos/video-e2e#technical-analysis')
  await page.getByTestId('tab-content-analysis').click()
  await expect(page.getByTestId('analysis-result-summary')).toBeVisible()
  await assertNoPageOverflow(page, '/videos/video-e2e#content-analysis')
})

test('手机首屏展示链接输入、身份状态和解析操作', async ({ page }, testInfo) => {
  const viewportWidth = testInfo.project.use.viewport?.width ?? 0
  test.skip(!testInfo.project.use.hasTouch || viewportWidth >= 768, '仅手机触控布局执行首屏门禁')

  await page.goto('/')
  const viewport = page.viewportSize()
  expect(viewport).not.toBeNull()
  const navigation = page.getByTestId('nav-home-mobile').locator('..')
  const navigationBox = await navigation.boundingBox()
  const visibleBottom = navigationBox?.y ?? viewport?.height ?? 0

  for (const [name, locator] of [
    ['链接输入框', page.getByTestId('video-url-input')],
    ['当前身份状态', page.locator('.parse-panel [data-testid="auth-status"]')],
    ['开始解析', page.getByTestId('parse-submit')],
  ] as const) {
    await expect(locator, `${name} 应在手机首屏可见`).toBeVisible()
    const box = await locator.boundingBox()
    expect(box, `${name} 应具有可测量区域`).not.toBeNull()
    expect(box?.y ?? -1, `${name} 不应位于首屏顶部之外`).toBeGreaterThanOrEqual(0)
    expect((box?.y ?? 0) + (box?.height ?? 0), `${name} 不应被底部导航遮挡`).toBeLessThanOrEqual(visibleBottom + 1)
  }

  const nextSectionBox = await page.locator('.capabilities').boundingBox()
  expect(nextSectionBox, '首屏之后的工作流内容应具有可测量区域').not.toBeNull()
  expect(nextSectionBox?.y ?? 0, '下一段内容不应露在固定底部导航下方').toBeGreaterThanOrEqual(viewport?.height ?? 0)
})

test('触控视口的关键操作目标至少为 44×44 px', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.use.hasTouch, '桌面鼠标项目不执行触控尺寸门禁')

  await page.goto('/')
  await assertTouchTarget(page.getByTestId('parse-submit'), '开始解析')
  await assertTouchTarget(page.getByTestId('access-mode-auto'), '自动解析模式')
  await assertTouchTarget(page.getByTestId('access-mode-anonymous'), '仅匿名解析模式')
  const homeNavigation = testInfo.project.use.viewport?.width && testInfo.project.use.viewport.width < 768
    ? page.getByTestId('nav-home-mobile')
    : page.getByTestId('nav-home-desktop')
  await assertTouchTarget(homeNavigation, '首页导航')

  await page.goto('/videos/video-e2e')
  const firstStreamControl = testInfo.project.use.viewport?.width && testInfo.project.use.viewport.width < 768
    ? page.getByTestId('stream-card').first()
    : page.getByRole('button', { name: /选择 720P 高清 H\.264/ })
  await assertTouchTarget(firstStreamControl, '媒体流选择')
  await assertTouchTarget(page.getByTestId('open-download-config'), '配置并下载')
  await assertTouchTarget(page.getByTestId('open-batch-download'), '多 P 批量下载')
  await assertTouchTarget(page.getByTestId('select-no-audio'), '不附加音频')
  await assertTouchTarget(page.getByTestId('tab-technical-analysis'), '技术分析标签')
  await page.getByTestId('tab-technical-analysis').tap()
  await expect(page.getByTestId('analysis-result-media')).toBeVisible()
  await assertTouchTarget(page.getByTestId('refresh-analysis-results'), '刷新分析结果')
  await page.getByTestId('tab-content-analysis').tap()
  await expect(page.getByTestId('analysis-result-asr')).toBeVisible()
  await assertTouchTarget(page.getByTestId('analysis-result-asr').getByRole('link', { name: '导出 1' }), '导出分析产物')

  await page.goto('/settings')
  await assertTouchTarget(page.getByRole('button', { name: '选择文件' }), '选择 Cookie 文件')
  await assertTouchTarget(page.locator('.settings-nav').getByRole('button', { name: /^下载/ }), '下载设置分组')

  await page.goto('/jobs')
  await assertTouchTarget(page.locator('.filter-controls .el-select').first(), '任务状态筛选')
  await assertTouchTarget(page.getByRole('button', { name: '查看详情' }).first(), '任务详情')

  await page.goto('/artifacts')
  const saveArtifact = testInfo.project.use.viewport?.width && testInfo.project.use.viewport.width < 768
    ? page.getByRole('button', { name: '保存到设备' }).first()
    : page.locator('.desktop-artifacts').getByRole('button', { name: '保存' }).first()
  await assertTouchTarget(saveArtifact, '保存产物')

  await page.goto('/diagnostics')
  await assertTouchTarget(page.getByRole('button', { name: '重新检查' }), '重新检查诊断')
  await assertTouchTarget(page.getByRole('button', { name: '导出脱敏诊断' }), '导出诊断')
})

test('手机关键流程可直接触控且不依赖 hover', async ({ page, testApi }, testInfo) => {
  const viewportWidth = testInfo.project.use.viewport?.width ?? 0
  test.skip(!testInfo.project.use.hasTouch || viewportWidth >= 768, '仅手机触控布局执行无 hover 门禁')

  testApi.setAuthenticated(true)
  await page.goto('/')
  await page.getByTestId('access-mode-anonymous').tap()
  await expect(page.getByTestId('access-mode-anonymous')).toHaveAttribute('aria-pressed', 'true')
  await page.getByTestId('nav-settings-mobile').tap()
  await expect(page).toHaveURL(/\/settings$/)
  await page.locator('.settings-nav').getByRole('button', { name: /^下载/ }).tap()
  await expect(page.getByRole('heading', { name: '下载默认值' })).toBeVisible()

  await page.goto('/videos/video-e2e')
  await page.getByTestId('open-batch-download').tap()
  const batchDrawer = page.getByTestId('batch-download-drawer')
  await expect(batchDrawer).toBeVisible()
  await expect(batchDrawer).toHaveClass(/btt/)
  await batchDrawer.locator('.el-drawer__close-btn').tap()
  const streamCard = page.getByTestId('stream-card').first()
  await streamCard.tap()
  await expect(streamCard).toHaveClass(/selected/)
  await page.getByTestId('open-download-config').tap()
  await expect(page.getByTestId('download-config-drawer')).toBeVisible()
})
