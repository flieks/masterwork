import { test, expect } from '@playwright/experimental-ct-react';
import { ViewToggle } from '~/features/assets/components/ViewToggle';

test('defaults to grid and persists a table selection to localStorage', async ({ mount, page }) => {
  await mount(<ViewToggle />);

  const grid = page.getByRole('button', { name: 'Grid view' });
  const table = page.getByRole('button', { name: 'Table view' });

  await expect(grid).toHaveAttribute('aria-pressed', 'true');
  await expect(table).toHaveAttribute('aria-pressed', 'false');

  await table.click();

  await expect(table).toHaveAttribute('aria-pressed', 'true');
  await expect(grid).toHaveAttribute('aria-pressed', 'false');

  const stored = await page.evaluate(() => localStorage.getItem('masterwork:asset-view'));
  expect(stored).toBe('"table"');
});
