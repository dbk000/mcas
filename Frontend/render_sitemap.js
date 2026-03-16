import { chromium } from '@playwright/test';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  
  const filePath = `file://${path.join(__dirname, 'mermaid_render.html')}`;
  
  try {
    console.log('Opening file...');
    await page.goto(filePath, { waitUntil: 'networkidle' });
    
    // Wait for the SVG to be rendered
    console.log('Waiting for Mermaid render...');
    await page.waitForSelector('svg', { timeout: 10000 });
    
    // Auto-crop to the content
    const element = await page.$('.mermaid');
    const boundingBox = await element.boundingBox();
    
    if (boundingBox) {
        await page.screenshot({ 
            path: '../docs/frontend_sitemap.png',
            clip: {
                x: boundingBox.x,
                y: boundingBox.y,
                width: boundingBox.width,
                height: boundingBox.height
            }
        });
        console.log('Sitemap PNG saved to docs/frontend_sitemap.png');
    } else {
        await page.screenshot({ path: '../docs/frontend_sitemap.png', fullPage: true });
        console.log('Sitemap PNG saved (full page) to docs/frontend_sitemap.png');
    }
  } catch (error) {
    console.error('Error rendering sitemap:', error);
  } finally {
    await browser.close();
  }
})();
