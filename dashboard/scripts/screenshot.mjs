/**
 * Screenshot helper for visual review.
 *
 * Usage: node scripts/screenshot.mjs [url] [outfile] [waitMs] [width] [height]
 *
 * Deliberately a plain script rather than a test: the point is to look at the
 * rendered dashboard, and a passing test suite says nothing about whether the
 * layout is legible.
 */
import { chromium } from '@playwright/test';

const url = process.argv[2] ?? 'http://127.0.0.1:4173/';
const out = process.argv[3] ?? 'preview.png';
const waitMs = Number(process.argv[4] ?? 11_000);
const width = Number(process.argv[5] ?? 1920);
const height = Number(process.argv[6] ?? 1080);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });

const errors = [];
page.on('console', (message) => {
  if (message.type() === 'error') errors.push(message.text());
});
page.on('pageerror', (error) => errors.push(String(error)));

await page.goto(url, { waitUntil: 'networkidle' });

// Let the mock device produce several analysis windows so the traces, the
// activation rows, and the memory ledger all have content to draw.
await page.waitForTimeout(waitMs);

await page.screenshot({ path: out, fullPage: true });

const summary = await page.evaluate(() => ({
  rows: document.querySelectorAll('.activation__row').length,
  ramSegments: document.querySelectorAll('.ram__segment').length,
  confidenceLines: document.querySelectorAll('.trace__line').length,
  transcript: document.querySelector('.transcript__text')?.textContent?.trim() ?? null,
  scrollWidth: document.documentElement.scrollWidth,
  clientWidth: document.documentElement.clientWidth,
}));

console.log(JSON.stringify({ out, width, height, summary, errors }, null, 2));
await browser.close();
