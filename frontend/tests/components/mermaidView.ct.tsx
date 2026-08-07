import { test, expect } from '@playwright/experimental-ct-react';
import { MermaidView } from '~/components/MermaidView';

test('valid source renders an SVG diagram', async ({ mount, page }) => {
  await mount(<MermaidView source={'flowchart TD\n  A[Start] --> B[End]'} />);

  await expect(page.getByRole('img', { name: 'Diagram' })).toBeVisible();
  await expect(page.locator('svg')).toBeVisible();
});

test('invalid source falls back to the raw code, never blank', async ({ mount, page }) => {
  const bad = 'this is definitely not a mermaid diagram';
  await mount(<MermaidView source={bad} />);

  await expect(page.getByText('Invalid diagram — showing source')).toBeVisible();
  await expect(page.getByText(bad)).toBeVisible();
  await expect(page.locator('svg')).toHaveCount(0);
});
