// Optional browser QA for the standalone local revision review.
import assert from 'node:assert/strict';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

if (!process.argv[2]) throw new Error('Usage: node scripts/review-smoke.mjs REVIEW.html [SCREENSHOT.png]');
const moduleName = process.env.MAKERSIM_PLAYWRIGHT_MODULE || 'playwright';
const { chromium } = await import(moduleName.startsWith('/') ? pathToFileURL(moduleName).href : moduleName);
const browser = await chromium.launch({ executablePath: process.env.MAKERSIM_CHROMIUM || '/usr/bin/chromium', headless: true, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
try {
  await page.goto(pathToFileURL(resolve(process.argv[2])).href);
  const count = await page.locator('#history > article').count();
  assert.ok(count >= 1);
  const labels = await page.locator('#history h2').allTextContents();
  assert.deepEqual(await page.locator('#comparison h2').allTextContents(), [labels[0], labels.at(-1)]);
  await page.evaluate(() => document.querySelectorAll('#comparison img').forEach(img => img.loading = 'eager'));
  await page.waitForFunction(() => Array.from(document.querySelectorAll('#comparison img')).every(img => img.complete && img.naturalWidth === 1200));
  if (process.argv[3]) await page.screenshot({ path: resolve(process.argv[3]), fullPage: true });
  await page.selectOption('#left-revision', String(count - 1));
  await page.selectOption('#right-revision', '0');
  assert.deepEqual(await page.locator('#comparison h2').allTextContents(), [labels.at(-1), labels[0]]);
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'Review must fit a narrow viewport');
  assert.deepEqual(errors, []);
  console.log('Offline review passed: embedded captures, revision selection, desktop and mobile layout.');
} finally { await browser.close(); }
