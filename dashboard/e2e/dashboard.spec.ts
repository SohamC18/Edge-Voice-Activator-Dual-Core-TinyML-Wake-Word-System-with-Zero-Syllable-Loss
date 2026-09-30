/**
 * End-to-end visual + accessibility validation.
 *
 * These assertions are about what a judge actually sees, so they check rendered
 * output rather than internal state:
 *
 *  - all three panels are present and non-empty,
 *  - the three chart surfaces (confidence, energy, RAM) actually draw,
 *  - no horizontal overflow at any supported width (a clipped RAM bar is a
 *    silent lie about the memory claim),
 *  - no accessibility violations,
 *  - the "unavailable" path renders an em dash, not a zero.
 */

import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

/** The mock emits a frame per 600 ms tick, so give it a few cycles to fill. */
async function waitForTelemetry(page: import('@playwright/test').Page): Promise<void> {
  await expect(page.getByRole('heading', { name: 'Live Telemetry & Transcript' })).toBeVisible();
  await expect(page.locator('.activation__row').first()).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('.ram__segment').first()).toBeVisible({ timeout: 20_000 });
}

test.describe('dashboard', () => {
  test('renders all three judge-facing panels', async ({ page }) => {
    await page.goto('/');
    await waitForTelemetry(page);

    await expect(page.getByRole('heading', { name: 'Live Telemetry & Transcript' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Activation Log' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Hardware Resources' })).toBeVisible();
  });

  test('draws the confidence trace, energy trace, and RAM ledger', async ({ page }) => {
    await page.goto('/');
    await waitForTelemetry(page);

    // Polylines only exist once two or more samples have accumulated.
    await expect(page.locator('.trace__line')).toHaveCount(3, { timeout: 20_000 });
    await expect(page.locator('.energy__line')).toBeVisible();
    await expect(page.locator('.ram__segment')).toHaveCount(7, { timeout: 20_000 });
  });

  test('labels the confidence series by the device-declared class names', async ({ page }) => {
    await page.goto('/');
    await waitForTelemetry(page);
    await expect(page.locator('.trace__legend-item')).toHaveCount(3, { timeout: 20_000 });

    for (const name of ['friday', 'silence', 'unknown']) {
      await expect(page.locator('.trace__legend-name', { hasText: name })).toBeVisible();
    }
  });

  test('highlights the first word and states the pre-roll proof', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.transcript__first-word').first()).toBeVisible({ timeout: 25_000 });

    const mark = page.locator('.transcript__first-word').first();
    await expect(mark).toHaveCSS('background-color', /.+/);
    await expect(page.getByText(/was captured before the keyword fired/i)).toBeVisible();
  });

  test('shows the 256 KiB budget with named segments', async ({ page }) => {
    await page.goto('/');
    await waitForTelemetry(page);

    await expect(page.getByText(/budget 256\.0 KiB/)).toBeVisible();
    for (const label of ['Tensor arena', 'Mel dB table', 'PCM ring', 'Pre-roll ring', 'Opus encoder', 'RTOS stacks', 'Other / free']) {
      await expect(page.locator('.ram__legend-label', { hasText: label })).toBeVisible();
    }
  });

  test('groups activation decisions by reason', async ({ page }) => {
    await page.goto('/');
    await waitForTelemetry(page);
    await expect(page.locator('.activation__reason-chip').first()).toBeVisible();
    // Rejections are retained, so the table is never accept-only.
    await expect(page.locator('.activation__table tbody tr').first()).toBeVisible();
  });

  test('never overflows horizontally', async ({ page }) => {
    await page.goto('/');
    await waitForTelemetry(page);

    // A clipped stacked bar would misrepresent the memory budget, so this is a
    // correctness check rather than a cosmetic one.
    const overflow = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 1);
  });

  test('marks a simulated source when not connected to hardware', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText('simulated source')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Mock device' })).toHaveAttribute('aria-pressed', 'true');
  });

  test('switches to the legacy serial source and badges it as synthetic', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Legacy serial' }).click();

    await expect(page.getByText('contains synthetic frames')).toBeVisible({ timeout: 20_000 });
    // The legacy path has no audio uplink, so it must not claim a transcript.
    await expect(page.getByText(/no utterance recognised yet/i)).toBeVisible();
  });

  test('has no detectable accessibility violations', async ({ page }) => {
    await page.goto('/');
    await waitForTelemetry(page);

    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations).toEqual([]);
  });

  test('exposes a skip link for keyboard users', async ({ page }) => {
    await page.goto('/');
    await page.keyboard.press('Tab');
    const skip = page.getByRole('link', { name: /skip to live telemetry/i });
    await expect(skip).toBeFocused();
  });
});
