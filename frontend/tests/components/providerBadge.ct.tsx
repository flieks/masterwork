import { test, expect } from '@playwright/experimental-ct-react';
import { ProviderBadge } from '~/features/assets/components/ProviderBadge';

test('global provider renders its id', async ({ mount }) => {
  const badge = await mount(<ProviderBadge provider="claude" />);
  await expect(badge).toHaveText('claude');
});

test('plugin provider renders as "plugin" with a read-only tooltip', async ({ mount }) => {
  const badge = await mount(<ProviderBadge provider="claude-plugin" />);
  await expect(badge).toHaveText('plugin');
  await expect(badge).toHaveAttribute('title', /read-only/i);
});
