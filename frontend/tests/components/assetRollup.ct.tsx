import { test, expect, type Page } from '@playwright/experimental-ct-react';
import { SessionsListPage } from '~/features/sessions/components/SessionsListPage';
import { windowSince } from '~/features/sessions/runs';
import { TestProviders } from './harness/TestProviders';
import { assetUsageRows, chatRun, factoryRunSummary } from './harness/runFixtures';
import { integration } from './harness/integrationFixtures';

/** The cross-session rollup: which skills and agents earn their keep. */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

/** One handler for both endpoints; records every URL so filters can be asserted. */
async function mockSessionsScreen(page: Page): Promise<{ urls: string[] }> {
  const urls: string[] = [];
  await page.route('**/api/v1/**', async (route) => {
    if (route.request().method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const url = route.request().url();
    if (url.includes('/observability/')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: CORS,
        body: JSON.stringify([integration()]),
      });
      return;
    }
    urls.push(url);
    const assets = url.includes('/coding-assets');
    const kind = new URL(url).searchParams.get('kind');
    const rows = assetUsageRows().filter((r) => !kind || r.kind === kind);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: CORS,
      body: JSON.stringify(assets ? rows : [factoryRunSummary()]),
    });
  });
  return { urls };
}

async function openAssets(page: Page) {
  await page.getByRole('tab', { name: 'Assets' }).click();
}

test('the rollup ranks every asset with its runs, uses and last use', async ({ mount, page }) => {
  await mockSessionsScreen(page);
  await mount(
    <TestProviders>
      <SessionsListPage />
    </TestProviders>,
  );

  await openAssets(page);

  const rows = page.getByRole('row');
  // Five assets plus the header row.
  await expect(rows).toHaveCount(6);

  // Server order is kept: uses descending, name breaking ties.
  const names = page.locator('tbody tr td:first-child');
  await expect(names).toHaveText([
    /subagent/,
    /agent-factory/,
    /frontend-dev/,
    /backend-developer/,
    /restart-backend/,
  ]);

  const frontendDev = rows.filter({ hasText: 'frontend-dev' });
  await expect(frontendDev).toContainText('skill');
  await expect(frontendDev).toContainText('3');
  // Every row reaches the asset page it names.
  await expect(frontendDev.getByRole('link')).toHaveAttribute('href', '/skills/frontend-dev');
});

test('the unresolved bucket is ranked but labelled, and links nowhere', async ({ mount, page }) => {
  await mockSessionsScreen(page);
  await mount(
    <TestProviders>
      <SessionsListPage />
    </TestProviders>,
  );
  await openAssets(page);

  const subagent = page.getByRole('row').filter({ hasText: 'subagent' });
  await expect(subagent).toContainText('unresolved');
  await expect(subagent.getByRole('link')).toHaveCount(0);
  await expect(subagent.getByTitle(/Claude Code deletes subagent transcripts/)).toBeVisible();
});

test('the kind filter and the since window reach the request', async ({ mount, page }) => {
  const { urls } = await mockSessionsScreen(page);
  await mount(
    <TestProviders>
      <SessionsListPage />
    </TestProviders>,
  );
  await openAssets(page);

  // All time by default — no `since` bound at all.
  await expect.poll(() => urls.some((u) => u.includes('/coding-assets'))).toBe(true);
  expect(urls.filter((u) => u.includes('/coding-assets')).at(-1)).not.toContain('since=');

  await page.getByRole('group', { name: 'Kind' }).getByRole('button', { name: 'Skills' }).click();
  await expect.poll(() => urls.some((u) => u.includes('kind=skill'))).toBe(true);
  // The agents are gone from the table, not just from the request.
  await expect(page.getByRole('row').filter({ hasText: 'subagent' })).toHaveCount(0);

  await page.getByRole('group', { name: 'Used' }).getByRole('button', { name: '24h' }).click();
  await expect.poll(() => urls.some((u) => u.includes('since='))).toBe(true);
});

test('the view lives in the URL so a rollup can be linked to', async ({ mount, page }) => {
  await mockSessionsScreen(page);
  await mount(
    <TestProviders initialEntries={['/sessions?view=assets']}>
      <SessionsListPage />
    </TestProviders>,
  );

  // Opens straight on the rollup, without a click.
  await expect(page.getByRole('tab', { name: 'Assets' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('row').filter({ hasText: 'agent-factory' })).toBeVisible();
});

test('windowSince turns a token into a bound, and all time into none', () => {
  const now = Date.parse('2026-08-09T12:00:00.000Z');
  expect(windowSince('all', now)).toBeUndefined();
  expect(windowSince('24h', now)).toBe('2026-08-08T12:00:00.000Z');
  expect(windowSince('7d', now)).toBe('2026-08-02T12:00:00.000Z');
});

test('the grid keeps the order the server chose, live first', async ({ mount, page }) => {
  // Deliberately not in time order: the backend puts the live run first even
  // though another run spoke more recently and then died.
  const live = factoryRunSummary({
    id: 'factory-live',
    title: 'the run happening right now',
    ended_at: null,
    last_event_at: new Date().toISOString(),
    started_at: '2026-08-01T00:00:00.000Z',
  });
  const newer = factoryRunSummary({ id: 'factory-newer', title: 'spoke later, then died' });
  const older = { ...chatRun(), phases: [], id: 'chat-older', title: 'oldest of the three' };

  await page.route('**/api/v1/**', async (route) => {
    if (route.request().method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: CORS,
      body: JSON.stringify([live, newer, older]),
    });
  });

  await mount(
    <TestProviders>
      <SessionsListPage />
    </TestProviders>,
  );

  // Nothing is re-sorted client-side, so the first card — top-left of the grid
  // — is the one the server put first.
  const cards = page.locator('a[href^="/sessions/"]');
  await expect(cards).toHaveCount(3);
  await expect(cards.nth(0)).toContainText('the run happening right now');
  await expect(cards.nth(1)).toContainText('spoke later, then died');
  await expect(cards.nth(2)).toContainText('oldest of the three');
});

test('the grid asks for roots only, and the status filter offers Abandoned', async ({
  mount,
  page,
}) => {
  const { urls } = await mockSessionsScreen(page);
  await mount(
    <TestProviders>
      <SessionsListPage />
    </TestProviders>,
  );

  // Stage children collapse into their parent instead of cluttering the grid.
  await expect.poll(() => urls.some((u) => u.includes('roots_only=true'))).toBe(true);

  await page
    .getByRole('group', { name: 'Status' })
    .getByRole('button', { name: 'Abandoned' })
    .click();
  await expect.poll(() => urls.some((u) => u.includes('status=abandoned'))).toBe(true);
});
