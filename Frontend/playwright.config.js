import { defineConfig, devices } from '@playwright/test'

const previewHost = process.env.PLAYWRIGHT_HOST || '127.0.0.1'
const previewPort = Number(process.env.PLAYWRIGHT_PORT || 4173)
const baseURL =
  process.env.PLAYWRIGHT_BASE_URL || `http://${previewHost}:${previewPort}`

export default defineConfig({
  testDir: './tests',
  timeout: 30000,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  expect: {
    timeout: 10000,
    toHaveScreenshot: {
      threshold: 0.2,
      maxDiffPixels: 100,
    },
  },
  fullyParallel: false,
  reporter: [
    ['list'],
    ['json', { outputFile: 'test-results/results.json' }],
  ],
  use: {
    baseURL,
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'off',
    viewport: { width: 1280, height: 720 },
  },
  webServer: {
    command: 'npm run preview:e2e',
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120000,
  },
  projects: [
    {
      name: 'desktop-chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'mobile-operator',
      use: {
        ...devices['Pixel 5'],
        viewport: { width: 390, height: 844 },
      },
    },
  ],
  outputDir: 'test-results/',
})
