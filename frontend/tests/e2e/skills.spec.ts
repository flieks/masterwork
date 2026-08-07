import { test, expect } from '@playwright/test';

// E2E runs against a real backend + dev server. It no-ops unless BACKEND_URL is
// set (the dev server is launched via playwright.config's webServer in that case).
test.describe('skills: list → detail', () => {
  test.skip(!process.env.BACKEND_URL, 'requires a running backend (set BACKEND_URL)');

  test('lists skills and opens a skill detail page', async ({ page }) => {
    await page.goto('/skills');

    await expect(page.getByRole('heading', { name: 'Skills' })).toBeVisible();

    // Works in either grid (cards are links) or table (rows navigate) — pick the
    // first skill link if present, otherwise the first table row.
    const firstLink = page.locator('a[href^="/skills/"]').first();
    await expect(firstLink).toBeVisible();
    await firstLink.click();

    await expect(page).toHaveURL(/\/skills\/.+/);
    await expect(page.getByRole('link', { name: /Back to skills/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /Edit/ })).toBeVisible();
  });
});
