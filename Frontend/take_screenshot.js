import { chromium } from '@playwright/test';

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1280, height: 1080 });
  try {
    console.log('Navigating to http://localhost:3000...');
    const response = await page.goto('http://localhost:3000', { 
      waitUntil: 'networkidle',
      timeout: 30000 
    });
    
    console.log('Response status:', response.status());
    
    // Wait for the main content to appear
    console.log('Waiting for [data-operator-surface]...');
    await page.waitForSelector('[data-operator-surface]', { timeout: 10000 });
    
    // Wait a bit more for lucide icons and transitions
    await page.waitForTimeout(2000);
    
    await page.screenshot({ path: 'screenshot.png', fullPage: true });
    console.log('Screenshot saved to screenshot.png');
  } catch (error) {
    console.error('Error taking screenshot:', error);
    // Take a screenshot anyway to see what's there
    await page.screenshot({ path: 'error_screenshot.png' });
    const content = await page.content();
    console.log('Page content length:', content.length);
  } finally {
    await browser.close();
  }
})();
