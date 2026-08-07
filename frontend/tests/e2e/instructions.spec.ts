import { test, expect } from '@playwright/test';

// E2E runs against a real backend + dev server. It no-ops unless BACKEND_URL is
// set (the dev server is launched via playwright.config's webServer in that case).
test.describe('global CLAUDE.md', () => {
  test.skip(!process.env.BACKEND_URL, 'requires a running backend (set BACKEND_URL)');

  test('opens from the sidebar and toggles the editor', async ({ page }) => {
    await page.goto('/skills');
    await page.getByRole('link', { name: 'CLAUDE.md' }).click();

    await expect(page).toHaveURL(/\/instructions$/);
    await expect(page.getByRole('heading', { name: 'CLAUDE.md' })).toBeVisible();

    // Either the file exists (Edit) or it doesn't (Create file) — both open the editor.
    const open = page.getByRole('button', { name: /Edit|Create file/ }).first();
    await open.click();

    await expect(page.getByRole('button', { name: 'Save' })).toBeDisabled(); // nothing changed yet
    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByRole('button', { name: /Edit|Create file/ }).first()).toBeVisible();
  });
});
