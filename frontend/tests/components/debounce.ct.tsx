import { test, expect } from '@playwright/experimental-ct-react';
import { DebounceHarness } from './harness/DebounceHarness';

test('debounced value trails the input by the delay', async ({ mount, page }) => {
  await mount(<DebounceHarness delay={300} />);

  const input = page.getByLabel('raw');
  const out = page.getByTestId('debounced');

  await expect(out).toHaveText('');
  await input.fill('hello');

  // Still debouncing shortly after typing.
  await expect(out).not.toHaveText('hello', { timeout: 150 });

  // Settles to the latest value once the window elapses.
  await expect(out).toHaveText('hello');
});
