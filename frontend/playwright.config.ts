import { defineConfig, devices } from '@playwright/test'

const requestedPort = Number(process.env.PLAYWRIGHT_PORT ?? 4173)
if (!Number.isInteger(requestedPort) || requestedPort < 1024 || requestedPort > 65_535) {
  throw new Error('PLAYWRIGHT_PORT 必须是 1024 到 65535 之间的整数')
}

const baseURL = `http://127.0.0.1:${requestedPort}`

export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [['github'], ['line']] : [['list']],
  expect: {
    timeout: 8_000,
  },
  use: {
    baseURL,
    colorScheme: 'light',
    locale: 'zh-CN',
    serviceWorkers: 'block',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  webServer: {
    command: `npm run build && npm run preview -- --port ${requestedPort} --strictPort`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [
    {
      name: 'chromium-mobile-360x800',
      use: {
        browserName: 'chromium',
        viewport: { width: 360, height: 800 },
        hasTouch: true,
        isMobile: true,
      },
    },
    {
      name: 'chromium-mobile-390x844',
      use: {
        browserName: 'chromium',
        viewport: { width: 390, height: 844 },
        hasTouch: true,
        isMobile: true,
      },
    },
    {
      name: 'chromium-tablet-768x1024',
      use: {
        browserName: 'chromium',
        viewport: { width: 768, height: 1024 },
        hasTouch: true,
      },
    },
    {
      name: 'chromium-desktop-1440x900',
      use: {
        browserName: 'chromium',
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: 'webkit-mobile-390x844',
      use: {
        ...devices['iPhone 13'],
        browserName: 'webkit',
        viewport: { width: 390, height: 844 },
      },
    },
    {
      name: 'webkit-desktop-1440x900',
      use: {
        ...devices['Desktop Safari'],
        browserName: 'webkit',
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: 'firefox-desktop-1440x900',
      use: {
        ...devices['Desktop Firefox'],
        browserName: 'firefox',
        viewport: { width: 1440, height: 900 },
      },
    },
  ],
})
