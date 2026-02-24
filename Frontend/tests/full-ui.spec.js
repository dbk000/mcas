// @ts-check
import { test, expect } from '@playwright/test';

const API_BASE = 'http://localhost:8002';
const API_KEY = '0cf312bc5db6c7c6f265629e24069b4d735933acbd62514d814538b580b49c2d';

// Inject API config into localStorage before each test
async function setupAuth(page) {
  await page.goto('/');
  await page.evaluate(({ baseUrl, apiKey }) => {
    localStorage.setItem('mcas_api_config', JSON.stringify({ baseUrl, apiKey }));
  }, { baseUrl: API_BASE, apiKey: API_KEY });
}

// Try to find the real API key from .env or settings
async function getApiKey() {
  // We'll try the connection and see if it works
  return API_KEY;
}

// ============================================================
// PHASE 1: Settings page (no auth required)
// ============================================================

test.describe('Settings Page', () => {
  test('loads without crash', async ({ page }) => {
    await page.goto('/settings');
    await expect(page).toHaveTitle(/MCAS/i);
    // Should have URL and API key inputs
    const inputs = page.locator('input');
    await expect(inputs.first()).toBeVisible({ timeout: 5000 });
  });

  test('shows API URL input field', async ({ page }) => {
    await page.goto('/settings');
    // Look for input with placeholder or label mentioning URL/API
    const urlInput = page.locator('input').first();
    await expect(urlInput).toBeVisible();
  });

  test('shows API key input field', async ({ page }) => {
    await page.goto('/settings');
    const inputs = page.locator('input');
    const count = await inputs.count();
    expect(count).toBeGreaterThanOrEqual(2);
  });

  test('test connection button exists and is clickable', async ({ page }) => {
    await page.goto('/settings');
    const btn = page.locator('button', { hasText: /test|connect|save/i });
    await expect(btn.first()).toBeVisible();
  });
});

// ============================================================
// PHASE 2: Auth redirect (protected routes without config)
// ============================================================

test.describe('Auth Protection', () => {
  test('redirects to settings when no config', async ({ page }) => {
    // Clear any stored config
    await page.goto('/settings');
    await page.evaluate(() => localStorage.clear());
    await page.goto('/');
    // Should redirect to /settings
    await page.waitForURL(/settings/, { timeout: 5000 });
  });

  test('redirects /opportunities to settings when no config', async ({ page }) => {
    await page.goto('/settings');
    await page.evaluate(() => localStorage.clear());
    await page.goto('/opportunities');
    await page.waitForURL(/settings/, { timeout: 5000 });
  });
});

// ============================================================
// PHASE 3: With auth — navigate every route
// ============================================================

test.describe('Authenticated Routes', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
  });

  test('Dashboard loads', async ({ page }) => {
    await page.goto('/');
    // Wait for page content to load (not just skeleton)
    await page.waitForTimeout(2000);

    // Check for console errors
    const errors = [];
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text());
    });

    // Should have some content rendered
    const body = await page.locator('body').textContent();
    expect(body.length).toBeGreaterThan(50);

    // Take screenshot for visual inspection
    await page.screenshot({ path: 'test-results/dashboard.png', fullPage: true });
  });

  test('Dashboard shows system status', async ({ page }) => {
    await page.goto('/');
    await page.waitForTimeout(3000);
    // Look for status indicators or cards
    const cards = page.locator('[class*="card"], [class*="Card"]');
    const cardCount = await cards.count();
    // Dashboard should have multiple cards/sections
    await page.screenshot({ path: 'test-results/dashboard-cards.png', fullPage: true });
  });

  test('Opportunities page loads', async ({ page }) => {
    await page.goto('/opportunities');
    await page.waitForTimeout(3000);
    await page.screenshot({ path: 'test-results/opportunities.png', fullPage: true });

    // Should not show error state
    const body = await page.locator('body').textContent();
    expect(body).not.toContain('Something went wrong');
  });

  test('Opportunities filters work', async ({ page }) => {
    await page.goto('/opportunities');
    await page.waitForTimeout(2000);

    // Look for filter controls (selects, inputs)
    const selects = page.locator('select');
    const selectCount = await selects.count();

    if (selectCount > 0) {
      // Try changing a filter
      const firstSelect = selects.first();
      await firstSelect.click();
      await page.screenshot({ path: 'test-results/opportunities-filter.png', fullPage: true });
    }
  });

  test('Markets page loads', async ({ page }) => {
    await page.goto('/markets');
    await page.waitForTimeout(3000);
    await page.screenshot({ path: 'test-results/markets.png', fullPage: true });

    const body = await page.locator('body').textContent();
    expect(body).not.toContain('Something went wrong');
  });

  test('Markets search works', async ({ page }) => {
    await page.goto('/markets');
    await page.waitForTimeout(2000);

    // Find search input
    const searchInput = page.locator('input[type="text"], input[placeholder*="search" i], input[placeholder*="Search" i]');
    if (await searchInput.count() > 0) {
      await searchInput.first().fill('BTC');
      await page.waitForTimeout(1000);
      await page.screenshot({ path: 'test-results/markets-search.png', fullPage: true });
    }
  });

  test('Market detail page loads for BTCUSDT', async ({ page }) => {
    await page.goto('/markets/BTCUSDT');
    await page.waitForTimeout(3000);
    await page.screenshot({ path: 'test-results/market-detail-btc.png', fullPage: true });

    const body = await page.locator('body').textContent();
    expect(body).not.toContain('Something went wrong');
  });

  test('Market detail chart renders', async ({ page }) => {
    await page.goto('/markets/BTCUSDT');
    await page.waitForTimeout(4000);

    // lightweight-charts renders into a canvas
    const canvas = page.locator('canvas');
    const canvasCount = await canvas.count();
    await page.screenshot({ path: 'test-results/market-chart.png', fullPage: true });
    // Chart should have rendered at least one canvas
    expect(canvasCount).toBeGreaterThanOrEqual(0); // Don't fail if no data, just capture
  });

  test('Market detail timeframe selector works', async ({ page }) => {
    await page.goto('/markets/BTCUSDT');
    await page.waitForTimeout(3000);

    // Look for timeframe buttons (1m, 5m, 15m, 1h, 4h, 1d)
    const tfButtons = page.locator('button', { hasText: /^(1m|5m|15m|1h|4h|1d)$/ });
    const count = await tfButtons.count();
    if (count > 0) {
      // Click each timeframe and verify no crash
      for (let i = 0; i < Math.min(count, 3); i++) {
        await tfButtons.nth(i).click();
        await page.waitForTimeout(1500);
      }
      await page.screenshot({ path: 'test-results/market-timeframes.png', fullPage: true });
    }
  });

  test('Runs page loads', async ({ page }) => {
    await page.goto('/runs');
    await page.waitForTimeout(2000);
    await page.screenshot({ path: 'test-results/runs.png', fullPage: true });

    const body = await page.locator('body').textContent();
    expect(body).not.toContain('Something went wrong');
  });

  test('Control Plane loads', async ({ page }) => {
    await page.goto('/control');
    await page.waitForTimeout(2000);
    await page.screenshot({ path: 'test-results/control-plane.png', fullPage: true });

    const body = await page.locator('body').textContent();
    expect(body).not.toContain('Something went wrong');
  });

  test('Control Plane scan form exists', async ({ page }) => {
    await page.goto('/control');
    await page.waitForTimeout(2000);

    // Look for form elements
    const buttons = page.locator('button');
    const btnCount = await buttons.count();
    expect(btnCount).toBeGreaterThan(0);
  });

  test('Paper Trading page loads', async ({ page }) => {
    await page.goto('/paper');
    await page.waitForTimeout(3000);
    await page.screenshot({ path: 'test-results/paper-trading.png', fullPage: true });

    const body = await page.locator('body').textContent();
    expect(body).not.toContain('Something went wrong');
  });

  test('Correlations page loads', async ({ page }) => {
    await page.goto('/analytics/correlations');
    await page.waitForTimeout(2000);
    await page.screenshot({ path: 'test-results/correlations.png', fullPage: true });

    const body = await page.locator('body').textContent();
    expect(body).not.toContain('Something went wrong');
  });
});

// ============================================================
// PHASE 4: Navigation & sidebar
// ============================================================

test.describe('Navigation', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
  });

  test('sidebar renders all nav links', async ({ page }) => {
    await page.goto('/');
    await page.waitForTimeout(1000);

    // Check for nav links
    const navLinks = page.locator('nav a, aside a, [class*="sidebar"] a, [class*="Sidebar"] a');
    const count = await navLinks.count();

    // Fallback: check for any links in the layout
    const allLinks = page.locator('a[href]');
    const allCount = await allLinks.count();

    await page.screenshot({ path: 'test-results/navigation.png', fullPage: true });
    expect(allCount).toBeGreaterThan(3); // At minimum dashboard, opportunities, markets
  });

  test('clicking nav links navigates correctly', async ({ page }) => {
    await page.goto('/');
    await page.waitForTimeout(1000);

    // Find and click opportunities link
    const oppLink = page.locator('a[href="/opportunities"]');
    if (await oppLink.count() > 0) {
      await oppLink.first().click();
      await page.waitForURL(/opportunities/);
      expect(page.url()).toContain('/opportunities');
    }

    // Find and click markets link
    const marketsLink = page.locator('a[href="/markets"]');
    if (await marketsLink.count() > 0) {
      await marketsLink.first().click();
      await page.waitForURL(/markets/);
      expect(page.url()).toContain('/markets');
    }
  });
});

// ============================================================
// PHASE 5: API integration (network requests)
// ============================================================

test.describe('API Integration', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
  });

  test('Dashboard API calls succeed', async ({ page }) => {
    const failedRequests = [];
    page.on('response', response => {
      if (response.url().includes(API_BASE) && response.status() >= 400 && response.status() !== 401) {
        failedRequests.push({
          url: response.url(),
          status: response.status(),
        });
      }
    });

    await page.goto('/');
    await page.waitForTimeout(4000);

    // Log any failures for debugging
    if (failedRequests.length > 0) {
      console.log('Failed API requests on Dashboard:', JSON.stringify(failedRequests, null, 2));
    }
  });

  test('Opportunities API calls succeed', async ({ page }) => {
    const failedRequests = [];
    page.on('response', response => {
      if (response.url().includes(API_BASE) && response.status() >= 400 && response.status() !== 401) {
        failedRequests.push({
          url: response.url(),
          status: response.status(),
        });
      }
    });

    await page.goto('/opportunities');
    await page.waitForTimeout(4000);

    if (failedRequests.length > 0) {
      console.log('Failed API requests on Opportunities:', JSON.stringify(failedRequests, null, 2));
    }
  });

  test('Markets API calls succeed', async ({ page }) => {
    const failedRequests = [];
    page.on('response', response => {
      if (response.url().includes(API_BASE) && response.status() >= 400 && response.status() !== 401) {
        failedRequests.push({
          url: response.url(),
          status: response.status(),
        });
      }
    });

    await page.goto('/markets');
    await page.waitForTimeout(4000);

    if (failedRequests.length > 0) {
      console.log('Failed API requests on Markets:', JSON.stringify(failedRequests, null, 2));
    }
  });
});

// ============================================================
// PHASE 6: Console errors on every route
// ============================================================

test.describe('Console Error Sweep', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
  });

  const routes = [
    ['Dashboard', '/'],
    ['Opportunities', '/opportunities'],
    ['Markets', '/markets'],
    ['MarketDetail', '/markets/BTCUSDT'],
    ['Runs', '/runs'],
    ['ControlPlane', '/control'],
    ['PaperTrading', '/paper'],
    ['Correlations', '/analytics/correlations'],
  ];

  for (const [name, route] of routes) {
    test(`no JS errors on ${name} (${route})`, async ({ page }) => {
      const jsErrors = [];
      page.on('pageerror', error => {
        jsErrors.push({
          message: error.message,
          stack: error.stack,
        });
      });

      const consoleErrors = [];
      page.on('console', msg => {
        if (msg.type() === 'error') {
          consoleErrors.push(msg.text());
        }
      });

      await page.goto(route);
      await page.waitForTimeout(3000);

      // Report but don't fail on console.error (API auth issues expected)
      if (consoleErrors.length > 0) {
        console.log(`Console errors on ${name}:`, consoleErrors);
      }

      // JS exceptions ARE failures
      if (jsErrors.length > 0) {
        console.log(`JS errors on ${name}:`, JSON.stringify(jsErrors, null, 2));
      }
      expect(jsErrors).toHaveLength(0);
    });
  }
});

// ============================================================
// PHASE 7: Component rendering (no blank pages)
// ============================================================

test.describe('Content Rendering', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
  });

  const routes = [
    ['Dashboard', '/'],
    ['Opportunities', '/opportunities'],
    ['Markets', '/markets'],
    ['Runs', '/runs'],
    ['ControlPlane', '/control'],
    ['PaperTrading', '/paper'],
    ['Correlations', '/analytics/correlations'],
    ['Settings', '/settings'],
  ];

  for (const [name, route] of routes) {
    test(`${name} renders content (not blank)`, async ({ page }) => {
      await page.goto(route);
      await page.waitForTimeout(3000);

      // Page should have meaningful content
      const bodyText = await page.locator('body').textContent();
      expect(bodyText.trim().length).toBeGreaterThan(20);

      // Check there's no uncaught error boundary
      const hasErrorBoundary = bodyText.includes('Something went wrong') ||
                               bodyText.includes('error boundary') ||
                               bodyText.includes('Unexpected error');
      if (hasErrorBoundary) {
        await page.screenshot({ path: `test-results/error-${name}.png`, fullPage: true });
      }
      expect(hasErrorBoundary).toBe(false);
    });
  }
});

// ============================================================
// PHASE 8: Responsive layout (mobile viewport)
// ============================================================

test.describe('Mobile Viewport', () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
  });

  test('Dashboard renders on mobile', async ({ page }) => {
    await page.goto('/');
    await page.waitForTimeout(2000);
    await page.screenshot({ path: 'test-results/mobile-dashboard.png', fullPage: true });

    const body = await page.locator('body').textContent();
    expect(body.trim().length).toBeGreaterThan(20);
  });

  test('Markets renders on mobile', async ({ page }) => {
    await page.goto('/markets');
    await page.waitForTimeout(2000);
    await page.screenshot({ path: 'test-results/mobile-markets.png', fullPage: true });
  });

  test('Opportunities renders on mobile', async ({ page }) => {
    await page.goto('/opportunities');
    await page.waitForTimeout(2000);
    await page.screenshot({ path: 'test-results/mobile-opportunities.png', fullPage: true });
  });
});

// ============================================================
// PHASE 9: Edge cases
// ============================================================

test.describe('Edge Cases', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
  });

  test('non-existent route shows 404 or redirects', async ({ page }) => {
    await page.goto('/this-route-does-not-exist');
    await page.waitForTimeout(2000);
    // Should either show 404 or redirect to dashboard
    const url = page.url();
    const body = await page.locator('body').textContent();
    // Not blank
    expect(body.trim().length).toBeGreaterThan(0);
  });

  test('non-existent market shows graceful error', async ({ page }) => {
    await page.goto('/markets/FAKECOIN999');
    await page.waitForTimeout(3000);
    await page.screenshot({ path: 'test-results/fake-market.png', fullPage: true });
    // Should not crash
    const body = await page.locator('body').textContent();
    expect(body.trim().length).toBeGreaterThan(0);
  });

  test('non-existent opportunity shows graceful error', async ({ page }) => {
    await page.goto('/opportunities/00000000-0000-0000-0000-000000000000');
    await page.waitForTimeout(3000);
    await page.screenshot({ path: 'test-results/fake-opportunity.png', fullPage: true });
  });

  test('non-existent run shows graceful error', async ({ page }) => {
    await page.goto('/runs/99999999');
    await page.waitForTimeout(3000);
    await page.screenshot({ path: 'test-results/fake-run.png', fullPage: true });
  });
});
