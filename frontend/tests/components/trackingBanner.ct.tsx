import { test, expect, type Page } from '@playwright/experimental-ct-react';
import type { ObservabilityIntegration } from '~/api/generated';
import { TrackingBanner } from '~/features/observability';
import { TestProviders } from './harness/TestProviders';
import { disconnected, integration, outdated, unavailable } from './harness/integrationFixtures';

/** One-click setup for session recording, and the one click that undoes it. */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

/**
 * Serves the integration list, and swaps in `after` once a connect/disconnect
 * POST lands — so a test sees the same refetch the real screen does.
 */
async function mockIntegrations(
  page: Page,
  before: ObservabilityIntegration,
  after?: ObservabilityIntegration,
): Promise<{ posted: string[] }> {
  const posted: string[] = [];
  let current = before;
  await page.route('**/api/v1/observability/**', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    if (request.method() === 'POST') {
      posted.push(request.url());
      if (after) current = after;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: CORS,
        body: JSON.stringify(current),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: CORS,
      body: JSON.stringify([current]),
    });
  });
  return { posted };
}

test('an unconnected agent gets a card that says what connecting will write', async ({
  mount,
  page,
}) => {
  await mockIntegrations(page, disconnected());

  await mount(
    <TestProviders>
      <TrackingBanner />
    </TestProviders>,
  );

  await expect(page.getByText('Record your coding sessions')).toBeVisible();
  await expect(page.getByText(/Connecting adds 7 hooks to/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Connect Claude Code' })).toBeEnabled();
});

test('connecting posts once and the banner collapses to a recording line', async ({
  mount,
  page,
}) => {
  const { posted } = await mockIntegrations(page, disconnected(), integration());

  await mount(
    <TestProviders>
      <TrackingBanner />
    </TestProviders>,
  );

  await page.getByRole('button', { name: 'Connect Claude Code' }).click();

  await expect(page.getByText('Recording Claude Code')).toBeVisible();
  await expect(page.getByText('Record your coding sessions')).toBeHidden();
  expect(posted).toHaveLength(1);
  expect(posted[0]).toContain('/observability/integrations/claude-code/connect');
});

test('a stale wiring offers a repair rather than a fresh install', async ({ mount, page }) => {
  await mockIntegrations(page, outdated());

  await mount(
    <TestProviders>
      <TrackingBanner />
    </TestProviders>,
  );

  await expect(page.getByText(/no longer on disk/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Repair' })).toBeEnabled();
});

test('an agent that cannot be wired up here says why and offers no button to press', async ({
  mount,
  page,
}) => {
  await mockIntegrations(page, unavailable());

  await mount(
    <TestProviders>
      <TrackingBanner />
    </TestProviders>,
  );

  await expect(page.getByText(/hasn't run on this machine yet/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Connect Claude Code' })).toBeDisabled();
});

test('a connected agent stays out of the way until Manage is opened', async ({ mount, page }) => {
  const { posted } = await mockIntegrations(page, integration(), disconnected());

  await mount(
    <TestProviders>
      <TrackingBanner />
    </TestProviders>,
  );

  await expect(page.getByText('Recording Claude Code')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Disconnect' })).toBeHidden();

  await page.getByRole('button', { name: 'Manage' }).click();
  await expect(page.getByText('/home/dev/.claude/settings.json')).toBeVisible();

  await page.getByRole('button', { name: 'Disconnect' }).click();

  await expect(page.getByText('Record your coding sessions')).toBeVisible();
  expect(posted[0]).toContain('/observability/integrations/claude-code/disconnect');
});
