// @ts-check
import { test, expect } from '@playwright/test'
import {
  configureAppAccess,
  mockHealthAndAuth,
  seedStorage,
} from './support/runtime.js'

async function expectNoHorizontalScroll(page) {
  const hasHorizontalScroll = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth
  )
  expect(hasHorizontalScroll).toBeFalsy()
}

async function expectPrimaryCtaAboveFold(page, locator) {
  await expect(locator).toBeVisible()
  const box = await locator.boundingBox()
  expect(box).not.toBeNull()
  expect(box.y + box.height).toBeLessThanOrEqual(page.viewportSize().height)
}

async function expectStablePosition(page, locator, maxShiftPx = 8) {
  const before = await locator.evaluate((element) => element.getBoundingClientRect().top)
  await page.waitForTimeout(600)
  const after = await locator.evaluate((element) => element.getBoundingClientRect().top)
  expect(Math.abs(after - before)).toBeLessThan(maxShiftPx)
}

test.describe('Public Funnel', () => {
  test('landing page keeps the primary CTA above the fold on mobile with stable metadata', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await seedStorage(page)
    await page.goto('/')

    const primaryCta = page.getByRole('link', { name: /Unlock Pro/i })
    await expect(page).toHaveTitle(/MCAS/i)
    await expect(page.locator('meta[name="description"]')).toHaveAttribute('content', /Glass Box|proof|honest/i)
    await expectNoHorizontalScroll(page)
    await expectPrimaryCtaAboveFold(page, primaryCta)
    await expectStablePosition(page, primaryCta)
  })

  test('winners page renders curated content and route-specific metadata', async ({ page }) => {
    await page.setViewportSize({ width: 393, height: 852 })
    await seedStorage(page)
    await page.goto('/winners')

    await expect(page).toHaveTitle(/Winners/i)
    await expect(page.getByRole('heading', { name: /Historical winners|Curated/i }).first()).toBeVisible()
    await expect(page.locator('meta[name="description"]')).toHaveAttribute('content', /curated|historical/i)
    await expectNoHorizontalScroll(page)
  })

  test('pricing page keeps the upgrade CTA above the fold on mobile', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await seedStorage(page)
    await page.goto('/pricing')

    const primaryCta = page.getByRole('link', { name: /Upgrade to Pro|Upgrade/i }).first()
    await expect(page).toHaveTitle(/Pricing/i)
    await expect(page.locator('meta[name="description"]')).toHaveAttribute('content', /Glass Box|metrics|operator/i)
    await expectNoHorizontalScroll(page)
    await expectPrimaryCtaAboveFold(page, primaryCta)
    await expectStablePosition(page, primaryCta)
  })

  test('checkout success sets local paid state', async ({ page }) => {
    await seedStorage(page)
    await page.goto('/checkout/success')

    await expect(page.getByRole('heading', { name: /browser is now marked as Pro|local activation complete/i })).toBeVisible()
    const tier = await page.evaluate(() => localStorage.getItem('mcas_local_access_tier'))
    const activatedAt = await page.evaluate(
      () => localStorage.getItem('mcas_local_access_activated_at'),
    )
    expect(tier).toBe('paid')
    expect(activatedAt).toBeTruthy()
  })

  test('checkout cancel returns the user to proof surfaces', async ({ page }) => {
    await seedStorage(page)
    await page.goto('/checkout/cancel')

    await expect(page.getByRole('heading', { name: /checkout flow stopped|Checkout Canceled/i })).toBeVisible()
    await expect(page.getByRole('link', { name: /Return to Pricing/i })).toBeVisible()
  })
})

test.describe('Paid Gate', () => {
  test('unpaid app access shows the paywall on mobile without horizontal scroll', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await seedStorage(page, { tier: 'free' })
    await page.goto('/app')

    const primaryCta = page.getByRole('link', { name: /Upgrade To Pro|Unlock Operator Access/i }).first()
    await expect(page.getByText(/Paid Operator Surface|paid gate/i).first()).toBeVisible()
    await expectNoHorizontalScroll(page)
    await expectPrimaryCtaAboveFold(page, primaryCta)
    await expectStablePosition(page, primaryCta)
  })

  test('paid but unconfigured users route to settings', async ({ page }) => {
    await seedStorage(page, { tier: 'paid' })
    await page.goto('/app')

    await page.waitForURL(/\/settings/)
    await expect(page.getByRole('heading', { name: /Settings/i })).toBeVisible()
  })
})

test.describe('App Routes', () => {
  test.beforeEach(async ({ page }) => {
    await mockHealthAndAuth(page)
  })

  test('strategy dashboard is usable on mobile with stubbed live data', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })

    await page.route('**/v1/strategies*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            strategies: [
              { id: 'U1', label: 'U1 Unicorn Ignition' },
              { id: 'S9', label: 'S9 Dormancy Breakout' },
            ],
          }
        }),
      })
    })

    await page.route('**/v1/opportunities*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            opportunities: [
              {
                id: 'U1:NVDA:15m',
                setup_id: 'U1',
                symbol: 'NVDA',
                tf: '15m',
                direction: 'LONG',
                score: 91.2,
                created_ts: '2026-03-15T14:30:00Z',
                sim_trace: { kline_lineage: { causality_verified: true } },
                reason: 'Leadership continuation',
              },
              {
                id: 'S9:BTCUSDT:1h',
                setup_id: 'S9',
                symbol: 'BTCUSDT',
                tf: '1h',
                direction: 'LONG',
                score: 86.5,
                created_ts: '2026-03-15T14:10:00Z',
                sim_trace: { kline_lineage: { causality_verified: true } },
                reason: 'Breakout expansion',
              },
            ],
          }
        }),
      })
    })

    await configureAppAccess(page, { returnTo: '/app/strategies' })

    await expect(page.getByRole('heading', { name: /Ranked strategy visibility|Strategy-ranked operator view/i })).toBeVisible()
    await expect(page.getByRole('link', { name: /Open Strategy/i }).first()).toBeVisible()
    await expectNoHorizontalScroll(page)
  })

  test('mocked opportunity detail shows the Glass Box badge', async ({ page }) => {
    await page.route('**/v1/opportunities/test-opp', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            id: 'test-opp',
            symbol: 'NVDA',
            timeframe: '15m',
            setup_id: 'U1',
            direction: 'LONG',
            score: 92.1,
            created_ts: '2026-03-15T14:30:00Z',
            reason: 'Mocked opportunity',
            sim_trace: {
              kline_lineage: {
                causality_verified: true,
                decision_ts: '2026-03-15T14:30:00Z',
                bar_start_ts: '2026-03-15T14:15:00Z',
                phases: [
                  { phase: 'ingest', state: 'verified', ts: '2026-03-15T14:29:55Z', detail: 'Input arrived before decision.' },
                ],
              },
            },
            payload: {
              entry_price: 100,
              stop_price: 95,
              tp1_price: 108,
              tp2_price: 112,
            },
          }
        }),
      })
    })

    await configureAppAccess(page, { returnTo: '/app/opportunities/test-opp' })

    await expect(page.getByText(/Glass Box/i).first()).toBeVisible()
    await expect(page.getByText(/Verified/i).first()).toBeVisible()
  })

  test('mocked simulation results show Avg Daily PF', async ({ page }) => {
    await page.route('**/v1/runs/123/simulation-results*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            run_status: 'SUCCESS',
            total_count: 3,
            summary: {
              win_rate: 0.66,
              total_r: 2.1,
              avg_r: 0.7,
              total_pnl_net: 120,
              profit_factor: 1.8,
              simulated_count: 3,
            },
            results: [
              { id: 'sim-1', symbol: 'BTCUSDT', timeframe: '1h', setup_id: 'S9', direction: 'LONG', pnl_net: 90, exit_ts: '2026-03-10T15:00:00Z' },
              { id: 'sim-2', symbol: 'ETHUSDT', timeframe: '1h', setup_id: 'S9', direction: 'LONG', pnl_net: -30, exit_ts: '2026-03-10T18:00:00Z' },
              { id: 'sim-3', symbol: 'SOLUSDT', timeframe: '1h', setup_id: 'S10', direction: 'SHORT', pnl_net: 60, exit_ts: '2026-03-11T16:00:00Z' },
            ],
          }
        }),
      })
    })

    await configureAppAccess(page, { returnTo: '/app/runs/123/simulation' })

    await expect(page.getByText(/Avg Daily PF/i).first()).toBeVisible()
  })

  test('control plane tabs support arrow-key navigation', async ({ page }) => {
    await page.route('**/v1/strategies*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            strategies: [
              { id: 'U1', label: 'U1 Unicorn Ignition' },
              { id: 'S9', label: 'S9 Dormancy Breakout' },
            ],
          },
        }),
      })
    })

    await page.route('**/v1/control/queue*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            queue: [],
          },
        }),
      })
    })

    await page.route('**/v1/markets*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            markets: [
              { symbol: 'BTCUSDT', status: 'active' },
              { symbol: 'ETHUSDT', status: 'active' },
            ],
          },
        }),
      })
    })

    await page.route('**/v1/control/paper/status*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            active: false,
          },
        }),
      })
    })

    await configureAppAccess(page, { returnTo: '/app/control' })

    const scanTab = page.getByRole('tab', { name: /^Scan$/i })
    const backtestTab = page.getByRole('tab', { name: /^Backtest$/i })

    await expect(scanTab).toHaveAttribute('aria-selected', 'true')
    await scanTab.focus()
    await page.keyboard.press('ArrowRight')

    await expect(backtestTab).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByRole('tabpanel')).toContainText(/Run Backtest/i)
  })

  test('simulation modal supports keyboard dismissal and multiselect interaction', async ({ page }) => {
    await page.route('**/v1/strategies*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            strategies: [
              { id: 'U1', label: 'U1 Unicorn Ignition' },
            ],
          },
        }),
      })
    })

    await page.route('**/v1/opportunities*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            opportunities: [
              {
                id: 'expired-1',
                symbol: 'NVDA',
                timeframe: '15m',
                setup_id: 'U1',
                direction: 'LONG',
                score: 91.2,
                payload: {
                  entry_price: 100,
                  stop_price: 96,
                  tp1_price: 106,
                  tp2_price: 110,
                },
              },
            ],
          },
        }),
      })
    })

    await configureAppAccess(page, { returnTo: '/app/opportunities' })

    await page.locator('select').first().selectOption('expired')

    const openModal = page.getByRole('button', { name: /Simulate Expired/i })
    await expect(openModal).toBeVisible()
    await openModal.focus()
    await openModal.click()

    const dialog = page.getByRole('dialog', { name: /Simulate Expired Opportunities/i })
    await expect(dialog).toBeVisible()

    const setupButton = page.getByRole('button', { name: /Filter setups/i })
    await setupButton.focus()
    await page.keyboard.press('ArrowDown')

    const listbox = page.getByRole('listbox')
    await expect(listbox).toBeVisible()
    await page.keyboard.press('Space')
    await page.keyboard.press('Escape')

    await expect(listbox).toHaveCount(0)
    await expect(setupButton).toContainText(/U1|1 selected/i)

    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden()
    await expect(openModal).toBeFocused()
  })
})
