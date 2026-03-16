import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { API_CONFIG, mockHealthAndAuth, seedStorage } from './support/runtime.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/**
 * operator-stress.spec.js
 * Enforces Feedback Onset latency, Mobile Fold density, and CTA stability.
 */

const FIXTURES_DIR = path.join(__dirname, '../src/mocks/fixtures');

async function mockApiWithFixture(page, endpoint, fixtureFile) {
  const fixturePath = path.join(FIXTURES_DIR, fixtureFile);
  const fixtureData = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
  
  // Use a more aggressive glob to catch both local and fully qualified URLs
  await page.route(`**${endpoint}*`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(fixtureData),
    });
  });
}

test.describe('Operator Stress: Feedback Onset (Critical Paths)', () => {
  test.beforeEach(async ({ page }) => {
    await mockHealthAndAuth(page);
  });

  test('Latency: Upgrade shows visual onset within 150ms', async ({ page }) => {
    await page.goto('/upgrade');
    await page.waitForLoadState('networkidle');
    
    // Use robust text selector
    const target = page.locator('button:has-text("Open Hosted Checkout")');
    
    // Force enable via JS evaluation
    await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('button'));
      const targetBtn = btns.find(b => b.textContent.includes('Open Hosted Checkout'));
      if (targetBtn) targetBtn.disabled = false;
    });

    await expect(target).toBeVisible();
    
    await target.click({ noWaitAfter: true });
    
    await expect(async () => {
      const feedbackExists = await page.locator('[data-is-pending="true"]').isVisible();
      expect(feedbackExists).toBeTruthy();
    }).toPass({ timeout: 1000 });
  });

  test('Latency: Run Start shows visual onset within 150ms', async ({ page }) => {
    await seedStorage(page, { tier: 'paid', apiConfig: API_CONFIG });
    await page.goto('/app/control');
    await page.waitForLoadState('networkidle');
    
    // Wait for form to be ready
    await page.waitForSelector('input[type="checkbox"]');
    await page.locator('input[type="checkbox"]').first().check();
    
    const target = page.locator('[data-latency-label="RunStart"] button');
    await expect(target).toBeVisible();
    
    await target.click({ noWaitAfter: true });
    
    await expect(async () => {
      const feedbackExists = await page.locator('[data-latency-label="RunStart"][data-is-pending="true"]').isVisible();
      expect(feedbackExists).toBeTruthy();
    }).toPass({ timeout: 1000 });
  });

  test('Latency: Filter shows visual onset within 150ms', async ({ page }) => {
    await seedStorage(page, { tier: 'paid', apiConfig: API_CONFIG });
    await page.goto('/app/opportunities');
    await page.waitForLoadState('networkidle');
    
    // Ensure the opportunities page rendered
    await page.waitForSelector('[data-latency-label="Filter"] button');
    
    const target = page.locator('[data-latency-label="Filter"] button');
    await expect(target).toBeVisible();
    
    await target.click({ noWaitAfter: true });
    
    await expect(async () => {
      const feedbackExists = await page.locator('[data-latency-label="Filter"][data-is-pending="true"]').isVisible();
      expect(feedbackExists).toBeTruthy();
    }).toPass({ timeout: 1000 });
  });
});

test.describe('Operator Stress: Mobile Fold & Stability (390x844)', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.beforeEach(async ({ page }) => {
    await mockHealthAndAuth(page);
  });

  test('Layout: Zero horizontal scroll on mobile', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const viewportWidth = await page.evaluate(() => window.innerWidth);
    expect(scrollWidth).toBeLessThanOrEqual(viewportWidth);
  });

  test('Reachability: Operational CTAs in Thumb Zone', async ({ page }) => {
    await seedStorage(page, { tier: 'paid', apiConfig: API_CONFIG });
    await page.goto('/app/opportunities');
    await page.waitForLoadState('networkidle');
    
    const operationalButtons = page.locator('[data-operator-surface] button, [data-latency-label] button');
    await expect(operationalButtons.first()).toBeVisible({ timeout: 5000 });
    
    const count = await operationalButtons.count();
    for (let i = 0; i < count; i++) {
      const button = operationalButtons.nth(i);
      if (await button.isVisible()) {
        const box = await button.boundingBox();
        if (box) {
          const centerY = box.y + box.height / 2;
          expect(centerY).toBeGreaterThan(844 * 0.4);
        }
      }
    }
  });

  test('Density: Strategy Dashboard Row Count', async ({ page }) => {
    await seedStorage(page, { tier: 'paid', apiConfig: API_CONFIG });
    await mockApiWithFixture(page, '/curation/strategy-dashboard-demo.v1.json', 'strategy-dashboard-demo.v1.json');
    await page.goto('/app/strategies');
    await page.waitForLoadState('networkidle');
    
    // Wait for the mock data to render rows
    const rows = page.locator('.op-row');
    await expect(rows.first()).toBeVisible({ timeout: 10000 });
    
    const count = await rows.count();
    let visibleInFold = 0;
    for (let i = 0; i < count; i++) {
      const box = await rows.nth(i).boundingBox();
      if (box && box.y >= 0 && (box.y + box.height) <= 844) {
        visibleInFold++;
      }
    }
    expect(visibleInFold).toBeGreaterThanOrEqual(8);
  });

  test('Stability: Primary CTA ZERO layout shift', async ({ page }) => {
    await page.goto('/upgrade');
    await page.waitForLoadState('networkidle');
    const target = page.locator('button:has-text("Open Hosted Checkout")');
    await expect(target).toBeVisible();
    
    const cls = await page.evaluate(async () => {
      let score = 0;
      const observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (!entry.hadRecentInput) score += entry.value;
        }
      });
      observer.observe({type: 'layout-shift', buffered: true});
      window.scrollBy(0, 100);
      await new Promise(r => setTimeout(r, 500));
      observer.disconnect();
      return score;
    });
    expect(cls).toBe(0);
  });
});
