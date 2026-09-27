import fs from 'node:fs'
import { test, expect } from '@playwright/test'
import { expandDemoFixture } from '../src/pages/trading-lab/demoFixture.js'

const sample = expandDemoFixture(
  JSON.parse(
    fs.readFileSync(
      new URL('../public/curation/trading-lab-demo.v1.json', import.meta.url),
      'utf8'
    )
  ),
  { '1h': 3600, '15m': 900 }
)

async function mockLive(context, { stale = false } = {}) {
  let failUniverse = false
  const mutations = []
  const candleReads = []
  const now = Date.now()
  const asOf = new Date(now - (stale ? 86400000 : 10000)).toISOString()
  await context.addInitScript(() =>
    localStorage.setItem('mcas_api_base_url', location.origin)
  )
  await context.route('**/health', (route) =>
    route.fulfill({ json: { status: 'healthy', pg_ms: 4, redis_ms: 2 } })
  )
  await context.route('**/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (request.method() !== 'GET') {
      if (!url.pathname.includes('telemetry')) mutations.push(url.pathname)
      return route.fulfill({ json: { ok: true } })
    }
    let data
    switch (url.pathname) {
      case '/v1/session/bootstrap':
        data = { has_session: true, has_app_access: true, auth: 'ok' }
        break
      case '/v1/stripe/subscription-status':
        data = { has_subscription: true, status: 'active' }
        break
      case '/v1/universe/heatmap':
        if (failUniverse)
          return route.fulfill({
            status: 503,
            json: { detail: 'Collector temporarily unavailable' },
          })
        data = {
          ...sample.heatmap,
          tiles: sample.heatmap.tiles.map((t) => ({
            ...t,
            as_of_ts: asOf,
            stale,
          })),
        }
        break
      case '/v1/opportunities':
        data = {
          opportunities: sample.signals.opportunities.map((t) => ({
            ...t,
            updated_ts: asOf,
          })),
        }
        break
      case '/v1/derivatives/dashboard':
        data = { ...sample.derivatives, data_as_of_ts: asOf }
        break
      case '/v1/equities/opportunities':
        data = sample.equities
        break
      case '/v1/ohlcv': {
        candleReads.push(url.search)
        const key = `${url.searchParams.get('symbol')}:${url.searchParams.get('tf')}`
        data = { ...sample.candles[key], collector_as_of_ts: asOf }
        // One forming bar must never reach the chart.
        data.candles = [
          ...(data.candles || []),
          { ...data.candles?.[0], timestamp: now, is_final: false },
        ]
        break
      }
      default:
        return route.fulfill({
          status: 404,
          json: { detail: 'Not in the lab contract' },
        })
    }
    return route.fulfill({
      json: { data, meta: { as_of_ts: new Date().toISOString() } },
    })
  })
  return {
    mutations,
    candleReads,
    fail: (value) => {
      failUniverse = value
    },
  }
}

test('preview renders all six views and remains explicitly sample data', async ({
  page,
}) => {
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/lab-preview')
  await page
    .getByRole('button', { name: 'Investigate SOLUSDT', exact: true })
    .click()
  await expect(page.getByTestId('selected-symbol')).toHaveText('SOLUSDT')
  await expect(
    page.getByRole('img', { name: /collector:SOLUSDT:1h: 84 finalized/ })
  ).toBeVisible()
  for (const view of [
    'Discovery',
    'Investigation',
    'Derivatives',
    'Equities',
    'Feed status',
    'Command',
  ]) {
    await page
      .getByRole('navigation', { name: 'Trading lab views' })
      .getByRole('button', { name: view, exact: true })
      .click()
    await expect(
      page.getByText('SAMPLE DATA · NOT LIVE', { exact: true })
    ).toBeVisible()
    await expect(page.locator('.lab-panel').first()).toBeVisible()
  }
  expect(errors).toEqual([])
})

test('linked windows follow selections; pinned windows stay put; profile restores', async ({
  page,
  context,
}) => {
  await page.goto('/lab-preview?view=discovery')
  await page
    .getByRole('button', { name: 'Investigate SOLUSDT', exact: true })
    .click()
  const follower = await context.newPage()
  const pinned = await context.newPage()
  await follower.goto('/lab-preview?view=investigation')
  await pinned.goto('/lab-preview?view=investigation&follow=0&symbol=BTCUSDT')
  await expect(follower.getByTestId('selected-symbol')).toHaveText('SOLUSDT')
  await page
    .getByRole('button', { name: 'Investigate ETHUSDT', exact: true })
    .click()
  await expect(follower.getByTestId('selected-symbol')).toHaveText('ETHUSDT')
  await expect(pinned.getByTestId('selected-symbol')).toHaveText('BTCUSDT')
  await page.getByRole('button', { name: 'Displays', exact: true }).click()
  await page.getByLabel('Display 1 name').fill('Projector left')
  await page.getByRole('button', { name: 'Save profile' }).click()
  await expect(
    page.getByRole('link', { name: 'Open Projector left' })
  ).toHaveAttribute('href', /name=Projector\+left/)
  await page.reload()
  await page.getByRole('button', { name: 'Displays', exact: true }).click()
  await expect(page.getByLabel('Display 1 name')).toHaveValue('Projector left')
  await follower.close()
  await pinned.close()
})

test('live route requires a real session', async ({ page }) => {
  await page.goto('/app/lab')
  await expect(page).toHaveURL(/settings.*returnTo/)
  await expect(page.locator('.trading-lab')).toHaveCount(0)
})

test('live contracts use source freshness, exclude forming bars and recover from failed reads', async ({
  page,
  context,
}) => {
  const backend = await mockLive(context, { stale: true })
  await page.goto('/app/lab')
  await page
    .getByRole('button', { name: 'Investigate SOLUSDT', exact: true })
    .click()
  await expect(
    page.getByRole('img', { name: /collector:SOLUSDT:1h: 84 finalized/ })
  ).toBeVisible()
  await expect(
    page
      .getByRole('region', { name: 'Price / volume', exact: true })
      .getByText('Stale', { exact: true })
  ).toBeVisible()
  await page.getByLabel('Chart timeframe').selectOption('15m')
  await expect(
    page.getByRole('img', { name: /collector:SOLUSDT:15m: 84 finalized/ })
  ).toBeVisible()
  backend.fail(true)
  await page.getByRole('button', { name: 'Refresh feeds', exact: true }).click()
  await expect(
    page.getByText('Update failed · showing cached records')
  ).toBeVisible()
  backend.fail(false)
  await page.getByRole('button', { name: 'Refresh feeds', exact: true }).click()
  await expect(
    page.getByText('Update failed · showing cached records')
  ).toHaveCount(0)
  const readsBeforePause = backend.candleReads.length
  await page.getByRole('button', { name: 'Pause feed updates' }).click()
  await page.getByLabel('Chart timeframe').selectOption('1h')
  await expect(
    page.getByRole('img', { name: /finalized candlesticks/ })
  ).toHaveCount(0)
  await page.waitForTimeout(200)
  expect(backend.candleReads).toHaveLength(readsBeforePause)
  await page.getByRole('button', { name: 'Refresh feeds', exact: true }).click()
  await expect(
    page.getByRole('img', { name: /collector:SOLUSDT:1h: 84 finalized/ })
  ).toBeVisible()
  expect(backend.mutations).toEqual([])
})

test('responsive desk, projector, portrait and phone layouts have no page overflow', async ({
  page,
}) => {
  await page.goto('/lab-preview')
  await expect(
    page.getByRole('button', { name: 'Investigate SOLUSDT', exact: true })
  ).toBeVisible()
  for (const viewport of [
    { width: 1920, height: 1080 },
    { width: 1080, height: 1920 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport)
    await page
      .getByLabel('Projection text size')
      .selectOption(viewport.width === 1920 ? '125' : '100')
    await expect
      .poll(() =>
        page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
      )
      .toBe(true)
    if (viewport.width >= 951) {
      for (const view of ['Command', 'Investigation', 'Discovery']) {
        await page.getByRole('button', { name: view, exact: true }).click()
        await expect
          .poll(() =>
            page.evaluate(
              () => document.documentElement.scrollHeight <= innerHeight + 1
            )
          )
          .toBe(true)
      }
    }
  }
  await page
    .getByRole('button', { name: 'Toggle light and dark theme' })
    .click()
  await expect(page.locator('.trading-lab')).toHaveAttribute(
    'data-lab-theme',
    'light'
  )
})
