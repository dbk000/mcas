import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const FIXTURES_DIR = path.resolve(__dirname, '../../src/mocks/fixtures')

const API_BASE_URL =
  process.env.PLAYWRIGHT_API_BASE_URL ||
  process.env.PLAYWRIGHT_BASE_URL ||
  'http://127.0.0.1:4173'

export const API_CONFIG = {
  baseUrl: API_BASE_URL,
  apiKey: 'test-api-key',
}

export async function seedStorage(page, { tier = 'free', apiConfig = null } = {}) {
  await page.addInitScript((state) => {
    localStorage.clear()
    localStorage.setItem('mcas_local_access_tier', state.tier)

    if (state.apiConfig) {
      localStorage.setItem('mcas_api_base_url', state.apiConfig.baseUrl)
    }
  }, { tier, apiConfig })
}

export async function mockHealthAndAuth(page) {
  let hasSession = false

  await page.route('**/health', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'healthy', redis_ms: 10, pg_ms: 15 }),
    })
  })

  await page.route('**/v1/session/bootstrap', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: {
          has_session: hasSession,
          has_app_access: hasSession,
          auth: hasSession ? 'ok' : 'missing',
          session_source: hasSession ? 'http_only_cookie' : 'none',
          entitlement_mode: 'preview_only',
          api_origin: API_CONFIG.baseUrl,
          checked_at: new Date().toISOString(),
        },
      }),
    })
  })

  await page.route('**/v1/session', async (route) => {
    if (route.request().method() === 'POST') {
      hasSession = true
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            has_session: true,
            has_app_access: true,
            auth: 'ok',
            session_source: 'http_only_cookie',
            entitlement_mode: 'preview_only',
            api_origin: API_CONFIG.baseUrl,
            checked_at: new Date().toISOString(),
          },
        }),
      })
      return
    }

    hasSession = false
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: {
          has_session: false,
          has_app_access: false,
          auth: 'missing',
          session_source: 'none',
          entitlement_mode: 'preview_only',
          api_origin: API_CONFIG.baseUrl,
          checked_at: new Date().toISOString(),
        },
      }),
    })
  })
}

export async function mockFixtureRoute(page, endpoint, fixtureFile) {
  const fixturePath = path.join(FIXTURES_DIR, fixtureFile)
  const fixtureData = JSON.parse(fs.readFileSync(fixturePath, 'utf8'))

  await page.route(`**${endpoint}*`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(fixtureData),
    })
  })
}

export async function configureAppAccess(page, { returnTo = '/app', apiConfig = API_CONFIG } = {}) {
  await seedStorage(page, { tier: 'paid', apiConfig })

  const settingsUrl = `/settings?from=playwright&returnTo=${encodeURIComponent(returnTo)}`
  await page.goto(settingsUrl)

  await page.locator('input[placeholder="https://api.example.com"]').fill(apiConfig.baseUrl)
  await page.locator('input[type="password"]').fill(apiConfig.apiKey)
  await page.getByRole('button', { name: /Connect Backend|Test Connection/i }).click()

  await page.waitForURL((url) => {
    const current = `${url.pathname}${url.search}`
    return current === returnTo || url.pathname === returnTo
  })
}
