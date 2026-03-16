import { existsSync } from 'node:fs'
import { expect, test } from '@playwright/test'
import {
  API_CONFIG,
  mockFixtureRoute,
  mockHealthAndAuth,
  seedStorage,
} from './support/runtime.js'

/**
 * visual-regressions.spec.js
 * Enforces visual consistency across primary operator surfaces.
 * STRICT: Only snapshots surfaces tagged with [data-operator-surface].
 * MECHANICAL GATE: Fails if WS1 density tokens AND spacing rules (Stylelint) are missing.
 * AUTHORED FIXTURES: Uses stable mock data for all snapshots.
 */

const STYLELINT_CONFIG_PATH = new URL('../.stylelintrc.json', import.meta.url)

async function ensureDensityStabilized(page) {
  if (!existsSync(STYLELINT_CONFIG_PATH)) {
    throw new Error('WS1 Spacing Enforcement Missing: .stylelintrc.json not found.')
  }

  const tokens = await page.evaluate(() => {
    const style = getComputedStyle(document.documentElement)
    return {
      padding: style.getPropertyValue('--op-card-padding').trim(),
      rowHeight: style.getPropertyValue('--op-row-height').trim(),
    }
  })

  if (!tokens.padding || !tokens.rowHeight || tokens.padding === '0' || tokens.rowHeight === '0') {
    throw new Error('WS1 Density Tokens Missing: --op-card-padding or --op-row-height not stabilized.')
  }
}

test.describe('Visual Regressions: Desktop (1280x720)', () => {
  test.use({ viewport: { width: 1280, height: 720 } })

  test('Snapshot: Landing Page', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await expect(page).toHaveScreenshot('landing-desktop.png', { fullPage: true })
  })

  test('Snapshot: Pricing', async ({ page }) => {
    await page.goto('/pricing')
    await page.waitForLoadState('networkidle')
    await ensureDensityStabilized(page)
    await expect(page).toHaveScreenshot('pricing-desktop.png', { fullPage: true })
  })

  test('Snapshot: Strategy Dashboard (STRICT + FIXTURE)', async ({ page }) => {
    await seedStorage(page, { tier: 'paid', apiConfig: API_CONFIG })
    await mockHealthAndAuth(page)
    await mockFixtureRoute(
      page,
      '/curation/strategy-dashboard-demo.v1.json',
      'strategy-dashboard-demo.v1.json',
    )
    await page.goto('/app/strategies')
    await page.waitForLoadState('networkidle')
    await ensureDensityStabilized(page)

    const dashboard = page.locator('[data-operator-surface]')
    await expect(dashboard).toBeVisible({ timeout: 10000 })
    await expect(dashboard).toHaveScreenshot('dashboard-desktop.png')
  })
})

test.describe('Visual Regressions: Mobile (390x844)', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('Snapshot: Mobile Landing', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await expect(page).toHaveScreenshot('landing-mobile.png', { fullPage: true })
  })

  test('Snapshot: Mobile Pricing', async ({ page }) => {
    await page.goto('/pricing')
    await page.waitForLoadState('networkidle')
    await ensureDensityStabilized(page)
    await expect(page).toHaveScreenshot('pricing-mobile.png', { fullPage: true })
  })

  test('Snapshot: Mobile Strategy Dashboard (STRICT + FIXTURE)', async ({ page }) => {
    await seedStorage(page, { tier: 'paid', apiConfig: API_CONFIG })
    await mockHealthAndAuth(page)
    await mockFixtureRoute(
      page,
      '/curation/strategy-dashboard-demo.v1.json',
      'strategy-dashboard-demo.v1.json',
    )
    await page.goto('/app/strategies')
    await page.waitForLoadState('networkidle')
    await ensureDensityStabilized(page)

    const dashboard = page.locator('[data-operator-surface]')
    await expect(dashboard).toBeVisible({ timeout: 10000 })
    await expect(dashboard).toHaveScreenshot('dashboard-mobile.png')
  })
})
