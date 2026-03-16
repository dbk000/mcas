import { expect, test } from '@playwright/test'
import {
  API_CONFIG,
  mockFixtureRoute,
  mockHealthAndAuth,
  seedStorage,
} from './support/runtime.js'

test.describe('Operator Density: Min Visible Rows (390x844)', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test.beforeEach(async ({ page }) => {
    await seedStorage(page, { tier: 'paid', apiConfig: API_CONFIG })
    await mockHealthAndAuth(page)
    await mockFixtureRoute(
      page,
      '/curation/strategy-dashboard-demo.v1.json',
      'strategy-dashboard-demo.v1.json',
    )
  })

  test('Strategy Dashboard shows at least 8 full rows in the mobile viewport', async ({
    page,
  }) => {
    await page.goto('/app/strategies')
    await page.waitForLoadState('networkidle')

    const rows = page.locator('.op-row, [data-operator-row]')
    await expect(rows.first()).toBeVisible({ timeout: 10000 })

    const rowCount = await rows.count()
    const viewportHeight = 844
    const headerHeight = 56
    const footerHeight = 40

    let visibleFullRows = 0
    for (let index = 0; index < rowCount; index += 1) {
      const box = await rows.nth(index).boundingBox()

      if (!box) {
        continue
      }

      const fullyVisible =
        box.y >= headerHeight &&
        box.y + box.height <= viewportHeight - footerHeight

      if (fullyVisible) {
        visibleFullRows += 1
      }
    }

    expect(visibleFullRows).toBeGreaterThanOrEqual(Math.min(rowCount, 8))
  })
})
