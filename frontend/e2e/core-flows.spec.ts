import type { Locator, Page } from '@playwright/test'
import { expect, runningJob, test } from './fixtures/api'

const trackedVideoUrl =
  'https://www.bilibili.com/video/BV1FYT5zkE1q/?spm_id_from=333.337.search-card.all.click&vd_source=test-only&p=2'

async function activate(locator: Locator, page: Page): Promise<void> {
  const viewport = page.viewportSize()
  if (viewport && viewport.width < 768) await locator.tap()
  else await locator.click()
}

function visibleAuthStatus(page: Page, text: string): Locator {
  return page.locator('[data-testid="auth-status"]:visible').filter({ hasText: text }).first()
}

function visibleText(page: Page, text: string): Locator {
  return page.getByText(text).filter({ visible: true }).first()
}

async function selectSettingsSection(page: Page, label: string): Promise<void> {
  const viewport = page.viewportSize()
  if (viewport && viewport.width >= 1200) {
    await page.getByTestId('settings-subnav').getByRole('button', { name: new RegExp(`^${label}`) }).click()
    return
  }
  await page.getByTestId('settings-section-select').click()
  await page.getByRole('option', { name: label, exact: true }).click()
}

for (const scenario of [
  { mode: 'auto', authenticated: true, label: '自动' },
  { mode: 'anonymous', authenticated: true, label: '仅匿名' },
  { mode: 'authenticated', authenticated: true, label: '使用登录态' },
] as const) {
  test(`首页以“${scenario.label}”模式提交规范化链接`, async ({ page, testApi }) => {
    testApi.setAuthenticated(scenario.authenticated)
    await page.goto('/')
    await expect(visibleAuthStatus(page, '大会员有效')).toBeVisible()
    await expect.poll(() => testApi.state.jobListRequestCount).toBeGreaterThan(0)

    const jobReadsBeforeNavigation = testApi.state.jobListRequestCount
    const settingsReadsBeforeNavigation = testApi.state.settingsReadRequestCount

    const modeButton = page.getByTestId(`access-mode-${scenario.mode}`)
    await activate(modeButton, page)
    await expect(modeButton).toHaveAttribute('aria-pressed', 'true')
    await page.getByTestId('video-url-input').fill(trackedVideoUrl)

    await Promise.all([
      page.waitForURL('**/videos/video-e2e'),
      activate(page.getByTestId('parse-submit'), page),
    ])

    const request = testApi.state.parseRequests.at(-1)
    expect(request).toBeDefined()
    expect(request?.accessMode).toBe(scenario.mode)
    expect(request?.url).toBe('https://www.bilibili.com/video/BV1FYT5zkE1q?p=2')
    expect(request?.url).not.toContain('spm_id_from')
    expect(request?.url).not.toContain('vd_source')
    expect(request?.browserCookieHeader).toBeNull()
    await expect(page.getByRole('heading', { name: 'E2E 测试专用：响应式视频解析样本' })).toBeVisible()
    await expect.poll(() => testApi.state.jobListRequestCount).toBeGreaterThan(jobReadsBeforeNavigation)
    await expect.poll(() => testApi.state.settingsReadRequestCount).toBeGreaterThan(settingsReadsBeforeNavigation)
  })
}

test('视频详情展示登录能力差异、媒体规格卡片并从抽屉创建下载任务', async ({ page, testApi }) => {
  testApi.setAuthenticated(true)
  await page.goto('/videos/video-e2e')
  await expect(visibleAuthStatus(page, '大会员有效')).toBeVisible()
  await expect(page.getByText('登录态可用，但本次未使用')).toBeVisible()
  await expect(page.getByRole('button', { name: /最佳兼容/ })).toHaveAttribute('aria-pressed', 'true')

  await activate(page.getByTestId('supplement-auth-quality'), page)
  await expect(page.getByText('本次已使用登录态')).toBeVisible()

  const viewport = page.viewportSize()
  if (viewport && viewport.width < 768) {
    const streamCards = page.getByTestId('stream-card')
    await expect(streamCards).toHaveCount(5)
    await expect(streamCards.filter({ hasText: '1080P+ 高码率' }).first()).toBeVisible()
    await activate(streamCards.filter({ hasText: 'H.264 / AVC' }).first(), page)
  } else {
    await expect(page.locator('.desktop-table')).toBeVisible()
    await expect(page.locator('.desktop-table').getByText('1080P+ 高码率').first()).toBeVisible()
    await page.getByRole('button', { name: /选择 1080P\+ 高码率 H\.264/ }).click()
  }

  await activate(page.getByTestId('verify-selected-streams'), page)
  await expect.poll(() => testApi.state.streamVerificationRequests).toEqual([
    { streamId: 'video-1080-avc', accessMode: 'authenticated' },
    { streamId: 'audio-aac-192', accessMode: 'authenticated' },
  ])
  await expect(page.getByTestId('verify-selected-streams')).toContainText('已读取验证')
  if (viewport && viewport.width < 768) {
    const selectedAudioBox = await page.locator('.audio-grid button.selected').boundingBox()
    const selectionBarBox = await page.locator('.selection-bar').boundingBox()
    expect(selectedAudioBox).not.toBeNull()
    expect(selectionBarBox).not.toBeNull()
    expect((selectedAudioBox?.y ?? 0) + (selectedAudioBox?.height ?? 0)).toBeLessThanOrEqual(
      selectionBarBox?.y ?? 0,
    )
  }

  await activate(page.getByTestId('open-video-preview'), page)
  const preview = page.locator('.preview-dialog')
  await expect(preview).toBeVisible()
  await expect(preview).toContainText('1080P+ 高码率')
  await expect(preview.getByTestId('preview-stage')).toBeVisible()
  await expect(preview.getByTestId('preview-video')).toBeAttached()
  await expect.poll(async () => preview.locator(
    '[data-testid="preview-video"]:visible, [role="status"]:visible, [role="alert"]:visible',
  ).count()).toBeGreaterThan(0)
  await expect.poll(() => testApi.state.previewRequests).toEqual([{
    videoStreamId: 'video-1080-avc',
    audioStreamId: 'audio-aac-192',
    accessMode: 'authenticated',
  }])
  await expect(page.locator('.el-message')).toHaveCount(0, { timeout: 10_000 })
  await preview.locator('.el-dialog__headerbtn').click()
  await expect(preview).toBeHidden()
  await expect.poll(() => testApi.state.previewDeletes).toEqual(['preview-e2e'])

  await activate(page.getByTestId('open-audio-preview'), page)
  await expect(preview).toBeVisible()
  await expect(preview).toContainText('试听音频')
  await expect(preview.getByTestId('preview-audio')).toBeAttached()
  await expect.poll(() => testApi.state.previewRequests.at(-1)).toEqual({
    videoStreamId: null,
    audioStreamId: 'audio-aac-192',
    accessMode: 'authenticated',
  })
  await preview.locator('.el-dialog__headerbtn').click()
  await expect(preview).toBeHidden()
  await expect.poll(() => testApi.state.previewDeletes).toEqual(['preview-e2e', 'preview-e2e'])

  await activate(page.getByTestId('open-download-config'), page)
  const drawer = page.getByTestId('download-config-drawer')
  await expect(drawer).toBeVisible()
  await expect(drawer.getByRole('heading', { name: '配置下载任务' })).toBeVisible()
  await expect(drawer.locator('input[maxlength="180"]')).toHaveValue('{title} - P{page}')
  await expect(drawer.getByRole('button', { name: /MKV/ })).toHaveClass(/active/)
  if (viewport && viewport.width < 768) {
    await expect(drawer).toHaveClass(/btt/)
  }

  await activate(page.getByTestId('create-download-job'), page)
  await expect(page.getByText('下载任务已创建')).toBeVisible()
  await expect.poll(() => testApi.state.downloadRequests.length).toBe(1)
  expect(testApi.state.downloadRequests[0]).toMatchObject({
    videoId: 'video-e2e',
    partId: 'part-2',
    videoStreamId: 'video-1080-avc',
    audioStreamId: 'audio-aac-192',
    accessMode: 'authenticated',
    processingMode: 'copy',
    container: 'mkv',
    includeSubtitle: true,
    includeCover: true,
    includeMetadata: true,
    cleanupTemporary: true,
    filename: '{title} - P{page}',
  })
})

test('视频流可明确选择不附加音频并提交 audioStreamId none', async ({ page, testApi }) => {
  await page.goto('/videos/video-e2e')
  const noAudio = page.getByTestId('select-no-audio')
  await activate(noAudio, page)
  await expect(noAudio).toHaveClass(/selected/)
  await activate(page.getByTestId('open-download-config'), page)
  const drawer = page.getByTestId('download-config-drawer')
  await expect(drawer).toBeVisible()
  await activate(drawer.getByTestId('create-download-job'), page)

  await expect.poll(() => testApi.state.downloadRequests.length).toBe(1)
  expect(testApi.state.downloadRequests[0]).toMatchObject({
    videoStreamId: 'video-720-avc',
    audioStreamId: 'none',
    accessMode: 'anonymous',
  })
})

test('可从媒体选择区直接创建纯音频下载任务', async ({ page, testApi }) => {
  await page.goto('/videos/video-e2e')
  await activate(page.getByRole('button', { name: '下载音频' }), page)
  const drawer = page.getByTestId('download-config-drawer')
  await expect(drawer).toBeVisible()
  await expect(drawer.getByRole('button', { name: /M4A/ })).toHaveClass(/active/)
  await activate(drawer.getByTestId('create-download-job'), page)

  await expect.poll(() => testApi.state.downloadRequests.length).toBe(1)
  expect(testApi.state.downloadRequests[0]).toMatchObject({
    videoStreamId: null,
    audioStreamId: 'audio-aac-192',
    container: 'm4a',
    processingMode: 'copy',
  })
})

test('详情页上传 Cookie 后安全返回并自动执行一次登录态重解析', async ({ page }) => {
  await page.goto('/videos/video-e2e')
  await activate(page.getByTestId('supplement-auth-quality'), page)
  await expect(page).toHaveURL(/\/settings\/auth\?.*returnTo=/)

  await page.getByTestId('cookie-file-input').setInputFiles({
    name: 'browser-session.test.json',
    mimeType: 'application/json',
    buffer: Buffer.from('[{"name":"test_only","value":"not-real","domain":".example.invalid"}]'),
  })
  await activate(page.getByTestId('cookie-upload-submit'), page)

  await expect(page).toHaveURL(/\/videos\/video-e2e(?:\?.*)?$/)
  await expect(page).not.toHaveURL(/useAuth=1/)
  await expect(page.getByText('本次已使用登录态')).toBeVisible()
  await expect(visibleText(page, '1080P+ 高码率')).toBeVisible()
})

test('设置页拒绝外域 returnTo，Cookie 上传后仍停留在站内', async ({ page }) => {
  await page.goto(`/settings/auth?returnTo=${encodeURIComponent('//evil.example/steal')}`)
  await page.getByTestId('cookie-file-input').setInputFiles({
    name: 'browser-session.test.json',
    mimeType: 'application/json',
    buffer: Buffer.from('[{"name":"test_only","value":"not-real","domain":".example.invalid"}]'),
  })
  await activate(page.getByTestId('cookie-upload-submit'), page)

  await expect(page).toHaveURL(/\/settings\/auth\?returnTo=/)
  expect(new URL(page.url()).hostname).toBe('127.0.0.1')
})

test('清除登录态会丢弃旧会员流并仅允许匿名任务', async ({ page, testApi }) => {
  testApi.setAuthenticated(true)
  await page.goto('/videos/video-e2e')
  await activate(page.getByTestId('supplement-auth-quality'), page)
  await expect(visibleText(page, '1080P+ 高码率')).toBeVisible()

  await page.locator('[data-testid^="nav-settings-"]:visible').click()
  await page.getByRole('button', { name: '彻底清除' }).click()
  await page.locator('.el-message-box').getByRole('button', { name: '彻底清除' }).click()
  await page.goBack()

  await expect(page.getByText('本次使用匿名模式')).toBeVisible()
  await expect(page.getByText('1080P+ 高码率')).toHaveCount(0)
  await activate(page.getByTestId('open-download-config'), page)
  await activate(page.getByTestId('create-download-job'), page)
  await expect.poll(() => testApi.state.downloadRequests.length).toBe(1)
  expect(testApi.state.downloadRequests[0]?.accessMode).toBe('anonymous')
})

test('多 P 抽屉按固定身份逐 P 选择流并顺序创建批量任务', async ({ page, testApi }) => {
  await page.goto('/videos/video-e2e')
  await activate(page.getByTestId('open-batch-download'), page)
  const drawer = page.getByTestId('batch-download-drawer')
  await expect(drawer).toBeVisible()
  await expect(drawer.getByText('已选 2 / 20')).toBeVisible()

  const danmakuSwitch = drawer.locator('.switch-list label').filter({ hasText: '弹幕 XML' }).locator('.el-switch')
  await activate(danmakuSwitch, page)
  await activate(drawer.getByTestId('create-download-batch'), page)

  await expect.poll(() => testApi.state.downloadBatchRequests.length).toBe(1)
  const requests = testApi.state.downloadBatchRequests[0]
  expect(requests?.map((request) => request.partId)).toEqual(['part-1', 'part-2'])
  expect(requests?.every((request) => (
    request.accessMode === 'anonymous'
    && request.includeDanmaku === true
    && request.container === 'mp4'
  ))).toBe(true)
  await expect(drawer.locator('.resolution-section article')).toHaveCount(2)
  await expect(drawer.getByText('已新建')).toHaveCount(2)
})

test('技术与内容标签按视频分 P 查询并并列保留成功、失败与证据结果', async ({ page, testApi }) => {
  await page.goto('/videos/video-e2e')

  await activate(page.getByTestId('tab-technical-analysis'), page)
  await expect.poll(() => testApi.state.analysisListRequestCount).toBeGreaterThan(0)
  expect(testApi.state.analysisQueries.at(-1)).toEqual({ videoId: 'video-e2e', partId: 'part-2' })

  const mediaResult = page.getByTestId('analysis-result-media')
  const audioResult = page.getByTestId('analysis-result-audio')
  const sceneResult = page.getByTestId('analysis-result-scenes')
  await expect(mediaResult).toContainText('H.264 / AVC')
  await expect(mediaResult).toContainText('1920 × 1080')
  await expect(audioResult).toContainText('-16.4 LUFS')
  await expect(audioResult).toContainText('静音时间线')
  await expect(sceneResult).toContainText('2.73 个/分钟')
  await expect(sceneResult).toContainText('关键帧定位')
  await expect(audioResult.getByRole('img', { name: /综合响度/ })).toBeVisible()
  await expect(sceneResult.getByRole('img', { name: /平均镜头长度/ })).toBeVisible()

  await activate(page.getByTestId('analysis-results-technical').getByRole('button', { name: '创建分析' }), page)
  const analysisDrawer = page.getByTestId('analysis-config-drawer')
  await expect(analysisDrawer).toBeVisible()
  await activate(analysisDrawer.getByRole('button', { name: /音频技术与启发式区段/ }), page)
  await activate(analysisDrawer.getByTestId('create-analysis-job'), page)
  await expect.poll(() => testApi.state.analysisRequests.length).toBe(1)
  expect(testApi.state.analysisRequests[0]?.features).toEqual(['metadata', 'audio'])

  const beforeRefresh = testApi.state.analysisListRequestCount
  await activate(page.getByTestId('refresh-analysis-results'), page)
  await expect.poll(() => testApi.state.analysisListRequestCount).toBeGreaterThan(beforeRefresh)

  await activate(page.getByTestId('tab-content-analysis'), page)
  const asrResult = page.getByTestId('analysis-result-asr')
  const failedOcr = page.getByTestId('analysis-result-ocr')
  const summaryResult = page.getByTestId('analysis-result-summary')
  await expect(page.getByTestId('analysis-result-basic')).toContainText('结构化基础概览')
  await expect(asrResult).toContainText('置信度：96%')
  await expect(asrResult).toContainText('<script>不会执行</script>，字幕内容始终作为纯文本渲染。')
  await expect(page.locator('script').filter({ hasText: '不会执行' })).toHaveCount(0)
  await expect(failedOcr).toContainText('OCR 模型未安装，本步骤未完成')
  await expect(failedOcr).toContainText('其他结果已经保留')
  await expect(summaryResult).toContainText('自动分析结果，可能存在误差')
  await expect(summaryResult).toContainText('分析结果会保留可定位的时间戳证据')
  await expect(summaryResult).toContainText('结果展示与安全边界')
  await expect(summaryResult).toContainText('local-extractive-evidence-analyzer · 2.0.0')
  await expect(summaryResult).toContainText('元数据 + 时间轴文本 + 结构化画面证据')
  await expect(summaryResult).toContainText('未识别视频内人物身份或画面对象')
  await expect(asrResult.getByRole('link', { name: '导出 1' })).toBeVisible()
  await expect(asrResult.getByRole('link', { name: /查看全部 5 项产物/ })).toBeVisible()

  await activate(asrResult.getByTestId('open-transcript-editor'), page)
  const transcriptEditor = page.getByTestId('transcript-edit-drawer')
  await expect(transcriptEditor).toBeVisible()
  await expect(transcriptEditor.getByRole('textbox').nth(1)).toHaveValue(
    '<script>不会执行</script>，字幕内容始终作为纯文本渲染。',
  )
  await activate(transcriptEditor.getByTestId('save-transcript-edit'), page)
  await expect.poll(() => testApi.state.analysisEdits.length).toBe(1)
  expect(testApi.state.analysisEdits[0]?.analysisId).toBe('analysis-asr-e2e')
  await expect(transcriptEditor).toBeHidden()
  await expect(page.getByTestId('analysis-result-asr')).toContainText('人工编辑修订 #1')
  await expect(page.getByTestId('analysis-result-asr')).toContainText('<script>不会执行</script>')
  await expect(page.locator('script').filter({ hasText: '不会执行' })).toHaveCount(0)

  const partSelector = page.locator('.part-bar .el-select')
  await partSelector.scrollIntoViewIfNeeded()
  await activate(partSelector, page)
  const firstPartOption = page.getByRole('option', { name: /P1 · 第一部分：匿名解析/ })
  await expect(firstPartOption).toBeVisible()
  await activate(firstPartOption, page)
  await expect(partSelector).toContainText('P1 · 第一部分：匿名解析')
  await expect.poll(() => testApi.state.analysisQueries.at(-1)?.partId).toBe('part-1')
  await expect(page.getByText('当前分 P 还没有内容分析结果')).toBeVisible()
})

test('任务中心在页面刷新后从 API 恢复进行中任务', async ({ page, testApi }) => {
  testApi.setJobs([runningJob()])
  await page.goto('/jobs')
  const card = page.getByTestId('job-card')
  await expect(card).toHaveCount(1)
  await expect(card).toContainText('下载视频流')
  await expect(card).toContainText('42%')
  await expect(page.getByRole('link', { name: '官方源视频' }).first()).toHaveAttribute(
    'href',
    'https://www.bilibili.com/video/BV1TEST/?p=2',
  )
  const firstLoadRequestCount = testApi.state.jobListRequestCount
  expect(firstLoadRequestCount).toBeGreaterThan(0)

  await page.reload()
  await expect(page.getByTestId('job-card')).toHaveCount(1)
  await expect(page.getByTestId('job-card')).toContainText('42%')
  await expect.poll(() => testApi.state.jobListRequestCount).toBeGreaterThan(firstLoadRequestCount)
})

test('任务中心展示伴随产物结果并允许暂停排队任务', async ({ page, testApi }) => {
  testApi.setJobs([
    {
      ...runningJob(),
      id: 'job-queued-e2e',
      videoTitle: '排队暂停测试',
      status: 'queued',
      phase: 'queued',
      progress: 0,
      startedAt: null,
    },
    {
      ...runningJob(),
      id: 'job-warning-e2e',
      videoTitle: '伴随产物警告测试',
      status: 'completed',
      phase: 'completed',
      progress: 100,
      finishedAt: '2026-07-14T08:00:00.000Z',
      companionOutcomes: { subtitle: 'failed', cover: 'not_available' },
      hasWarnings: true,
    },
    {
      ...runningJob(),
      id: 'job-not-available-e2e',
      videoTitle: 'only-not-available',
      status: 'completed',
      phase: 'completed',
      progress: 100,
      finishedAt: '2026-07-14T08:00:00.000Z',
      companionOutcomes: { cover: 'not_available' },
      hasWarnings: true,
    },
  ])
  await page.goto('/jobs')

  const warningCard = page.getByTestId('job-card').filter({ hasText: '主媒体已完成，部分随附内容失败' })
  await expect(warningCard).toContainText('公开字幕未能保存')
  await expect(warningCard).toContainText('封面不可用，不影响主媒体任务完成')

  const notAvailableCard = page.getByTestId('job-card').filter({ hasText: 'only-not-available' })
  await expect(notAvailableCard.locator('.job-warning')).toHaveCount(0)
  await expect(notAvailableCard.locator('.job-notice')).toBeVisible()

  const queuedCard = page.getByTestId('job-card').filter({ hasText: '排队暂停测试' })
  await activate(queuedCard.getByRole('button', { name: '暂停' }), page)
  await expect(queuedCard).toContainText('已暂停')
})

test('任务中心使用服务端分页且活动任务同步不污染当前页', async ({ page, testApi }) => {
  testApi.setJobs(Array.from({ length: 25 }, (_, index) => ({
    ...runningJob(),
    id: `completed-${index + 1}`,
    status: 'completed' as const,
    phase: 'completed',
    progress: 100,
    finishedAt: '2026-07-14T08:00:00.000Z',
  })))
  await page.goto('/jobs')

  await expect(page.getByTestId('job-card')).toHaveCount(20)
  await expect(page.getByText('第 1 页 · 共 25 个任务')).toBeVisible()
  await activate(page.locator('.el-pagination .btn-next'), page)
  await expect(page.getByTestId('job-card')).toHaveCount(5)
  await expect(page.getByText('第 2 页 · 共 25 个任务')).toBeVisible()
})

test('任务中心支持选择终态任务并批量删除', async ({ page, testApi }) => {
  testApi.setJobs([
    { ...runningJob(), id: 'job-delete-one', status: 'completed', phase: 'completed', progress: 100, finishedAt: '2026-07-14T08:00:00.000Z' },
    { ...runningJob(), id: 'job-delete-two', status: 'failed', phase: 'failed', progress: 70, finishedAt: '2026-07-14T08:00:00.000Z' },
  ])
  await page.goto('/jobs')
  await activate(page.locator('.job-group-heading .el-checkbox').first(), page)
  await expect(page.locator('.batch-bar')).toContainText('已选择 2 条任务')
  await activate(page.locator('.batch-bar').getByRole('button', { name: '批量删除' }), page)
  await page.locator('.el-message-box').getByRole('button', { name: '批量删除' }).click()
  await expect.poll(() => testApi.state.jobBatchDeletions).toEqual([['job-delete-one', 'job-delete-two']])
  await expect(page.getByTestId('job-card')).toHaveCount(0)
})

test('最近解析页面保留紧凑的单项删除', async ({ page, testApi }) => {
  await page.goto('/recent')
  await expect(page.getByRole('heading', { name: '最近解析' })).toBeVisible()
  await expect(page.locator('.recent-view .el-checkbox')).toHaveCount(0)
  await expect(page.getByTestId('recent-card')).toHaveCount(1)
  await activate(page.getByRole('button', { name: '删除解析记录' }), page)
  const confirmation = page.locator('.compact-delete-confirm')
  await expect(confirmation).toBeVisible()
  expect((await confirmation.boundingBox())?.width ?? 999).toBeLessThanOrEqual(380)
  await confirmation.getByRole('button', { name: '删除', exact: true }).click()
  await expect.poll(() => testApi.state.videoDeletions).toEqual(['video-e2e'])
  await expect(page.getByTestId('recent-card')).toHaveCount(0)
  await expect(page.getByText('还没有最近解析记录')).toBeVisible()
})

test('设置页可上传脱敏测试 Cookie 文件并彻底清除登录态', async ({ page, testApi }) => {
  await page.goto('/settings/auth')
  await expect(page.getByRole('heading', { name: 'Cookie 登录态' })).toBeVisible()

  const testOnlyCredentialMarker = 'not-a-real-credential-e2e-only'
  await page.getByTestId('cookie-file-input').setInputFiles({
    name: 'browser-session.test.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify([
      {
        name: 'test_only_session',
        value: testOnlyCredentialMarker,
        domain: '.example.invalid',
        path: '/',
        secure: true,
        httpOnly: true,
      },
    ])),
  })
  await expect(page.getByText('browser-session.test.json')).toBeVisible()
  await activate(page.getByTestId('cookie-upload-submit'), page)

  await expect(page.getByText('当前 Bilibili 身份')).toBeVisible()
  await expect(page.locator('.account-card').getByText('测***号')).toBeVisible()
  expect(testApi.state.cookieUploadCount).toBe(1)
  await expect(page.locator('body')).not.toContainText(testOnlyCredentialMarker)
  const browserStorage = await page.evaluate(() => `${localStorage.length}:${sessionStorage.length}`)
  expect(browserStorage).toBe('0:0')

  await page.getByRole('button', { name: '彻底清除' }).click()
  const confirmation = page.locator('.el-message-box')
  await expect(confirmation).toContainText('内存 Cookie、本机加密密文与身份缓存')
  await confirmation.getByRole('button', { name: '彻底清除' }).click()

  await expect(visibleAuthStatus(page, '匿名模式')).toBeVisible()
  await expect(page.getByText('当前 Bilibili 身份')).toHaveCount(0)
  expect(testApi.state.cookieClearCount).toBe(1)
})

test('产物删除前明确二次确认记录与文件范围', async ({ page, testApi }) => {
  await page.goto('/artifacts')
  await activate(page.getByTestId('artifact-group-toggle').first(), page)
  await expect(page.getByText(/受管产物 16(?:\.0)? MB/)).toBeVisible()
  expect(testApi.state.unhandledRequests).not.toContain('GET /artifacts/storage')
  const viewport = page.viewportSize()
  const filename = viewport && viewport.width < 768
    ? page.locator('.mobile-artifacts').getByText('E2E-测试专用-第二部分.mp4')
    : page.locator('.desktop-artifacts').getByText('E2E-测试专用-第二部分.mp4')
  await expect(filename).toBeVisible()

  const deleteButton = viewport && viewport.width < 768
    ? page.getByRole('button', { name: '删除产物' })
    : page.locator('.desktop-artifacts .el-button--danger').first()
  await activate(deleteButton, page)

  const confirmation = page.locator('.el-message-box')
  await expect(confirmation).toContainText('选择“记录与文件”')
  await expect(confirmation.getByRole('button', { name: '仅删除记录' })).toBeVisible()
  await confirmation.getByRole('button', { name: '记录与文件' }).click()

  await expect.poll(() => testApi.state.artifactDeletions).toEqual([
    { artifactId: 'artifact-e2e', deleteFile: true },
  ])
  await expect(page.getByText('E2E-测试专用-第二部分.mp4')).toHaveCount(0)
})

test('产物中心支持批量选择并彻底删除文件', async ({ page, testApi }) => {
  await page.goto('/artifacts')
  await activate(page.getByTestId('artifact-group-toggle').first(), page)
  const viewport = page.viewportSize()
  const selection = viewport && viewport.width < 768
    ? page.locator('.mobile-artifacts .artifact-checkbox').first()
    : page.locator('.desktop-artifacts .el-table__body-wrapper .el-checkbox').first()
  await activate(selection, page)
  const batchBar = page.locator('.batch-bar')
  await expect(batchBar).toContainText('已选择 1 个产物')
  await activate(batchBar.getByRole('button', { name: '批量删除' }), page)
  await page.locator('.el-message-box').getByRole('button', { name: '全部彻底删除' }).click()

  await expect.poll(() => testApi.state.artifactBatchDeletions).toEqual([{
    artifactIds: ['artifact-e2e'],
    deleteFile: true,
  }])
  await expect(page.getByTestId('artifact-card')).toHaveCount(0)
})

test('设置分组可修改保存并进入诊断页导出脱敏报告', async ({ page, testApi }) => {
  await page.goto('/settings/auth')

  const settingsSections = [
    { label: '下载', heading: '下载默认值' },
    { label: '存储', heading: '存储与清理' },
    { label: '分析', heading: '分析模型' },
    { label: '网络', heading: '网络策略' },
    { label: '隐私', heading: '隐私与历史' },
  ]
  for (const section of settingsSections) {
    await selectSettingsSection(page, section.label)
    await expect(page.getByRole('heading', { name: section.heading })).toBeVisible()
  }

  await selectSettingsSection(page, '下载')
  const filenameTemplate = page.locator('input[maxlength="180"]')
  await filenameTemplate.fill('{title}-{quality}-e2e')
  const saveButton = page.getByRole('button', { name: '保存设置' })
  await saveButton.scrollIntoViewIfNeeded()
  await saveButton.click()
  await expect.poll(() => testApi.state.settingsUpdates.length).toBe(1)
  expect(testApi.state.settingsUpdates[0]?.download.filenameTemplate).toBe('{title}-{quality}-e2e')

  await page.locator('.page-header').getByRole('button', { name: '关于与诊断' }).click()
  await expect(page).toHaveURL(/\/diagnostics$/)
  await expect(page.getByRole('heading', { name: '组件健康' })).toBeVisible()
  await expect(page.getByText('FFmpeg / FFprobe')).toBeVisible()
  await expect(page.getByText('诊断数据已脱敏')).toBeVisible()

  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出脱敏诊断' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toMatch(/^biliscope-diagnostics-\d{4}-\d{2}-\d{2}\.json$/)
})
