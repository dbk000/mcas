import { chromium } from '@playwright/test';

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  
  page.on('console', msg => console.log('BROWSER LOG:', msg.text()));
  page.on('pageerror', err => console.error('BROWSER ERROR:', err.message));
  page.on('requestfailed', request => {
    console.log('BROWSER REQUEST FAILED:', request.url(), request.failure().errorText);
  });

  await page.setViewportSize({ width: 1280, height: 1080 });
  try {
    console.log('Navigating to http://localhost:3000...');
    const response = await page.goto('http://localhost:3000', { 
      waitUntil: 'networkidle',
      timeout: 30000 
    });
    
    console.log('Response status:', response.status());
    
    // Wait for 5 seconds to see if anything loads
    await page.waitForTimeout(5000);
    
    await page.screenshot({ path: 'screenshot_v2.png', fullPage: true });
    console.log('Screenshot saved to screenshot_v2.png');
    
    const html = await page.content();
    console.log('HTML length:', html.length);
    // Print a bit of the body to see if there's anything inside #root
    const rootHtml = await page.evaluate(() => document.getElementById('root')?.innerHTML);
    console.log('Root innerHTML length:', rootHtml?.length || 0);
    if (rootHtml && rootHtml.length < 500) {
      console.log('Root innerHTML:', rootHtml);
    }
  } catch (error) {
    console.error('Error in script:', error);
  } finally {
    await browser.close();
  }
})();
