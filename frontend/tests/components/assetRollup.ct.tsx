import { test, expect, type Page } from '@playwright/experimental-ct-react';
import type { AssetSessionUse } from '~/api/generated';
import { SessionsListPage } from '~/features/sessions/components/SessionsListPage';
import { windowSince } from '~/features/sessions/runs';
import { AssetScopeHarness } from './harness/AssetScopeHarness';
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

/**
 * Masterwork analyses assets by running Claude over them, and those runs Read
 * every linked SKILL.md — so counting them ranks assets by inspection. The
 * backend leaves them out by default; these cover the way to look anyway.
 */

/** Same rows, but as they read once masterwork's own analysis runs are counted. */
function inspectionRows() {
  return assetUsageRows().map((row) => ({
    ...row,
    sessions: row.sessions + 6,
    uses: row.uses + 12,
  }));
}

async function mockInspectionScope(page: Page): Promise<{ urls: string[] }> {
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
    // The backend does the counting — the flag has to reach it either way.
    const inspected = new URL(url).searchParams.get('include_inspection') === 'true';
    const rows = inspected ? inspectionRows() : assetUsageRows();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: CORS,
      body: JSON.stringify(url.includes('/coding-assets') ? rows : [factoryRunSummary()]),
    });
  });
  return { urls };
}

test('the rollup excludes inspection runs until the toggle asks for them', async ({
  mount,
  page,
}) => {
  const { urls } = await mockInspectionScope(page);
  await mount(
    <TestProviders>
      <SessionsListPage />
    </TestProviders>,
  );
  await openAssets(page);

  const frontendDev = page.getByRole('row').filter({ hasText: 'frontend-dev' });
  await expect(frontendDev).toContainText('3');
  await expect.poll(() => urls.some((u) => u.includes('/coding-assets'))).toBe(true);
  expect(urls.filter((u) => u.includes('/coding-assets')).at(-1)).not.toContain(
    'include_inspection=true',
  );

  await page.getByRole('button', { name: 'Include inspection runs' }).click();

  await expect.poll(() => urls.some((u) => u.includes('include_inspection=true'))).toBe(true);
  // The counts rise, and the table says why they did.
  await expect(frontendDev).toContainText('15');
  await expect(page.getByText(/rank assets by inspection as much as by use/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Hide inspection runs' })).toBeVisible();
});

test('the toggle says what an inspection run is', async ({ mount, page }) => {
  await mockInspectionScope(page);
  await mount(
    <TestProviders>
      <SessionsListPage />
    </TestProviders>,
  );
  await openAssets(page);

  const toggle = page.getByRole('button', { name: 'Include inspection runs' });
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await expect(toggle).toHaveAttribute('title', /Read every linked asset's SKILL\.md/);
  await expect(toggle).toHaveAttribute('title', /rather than by the work they did/);
});

test('the per-asset drill-in follows the same scope as the table', async ({ mount, page }) => {
  const use = (sessionId: string, uses: number): AssetSessionUse => ({
    session_id: sessionId,
    title: `${sessionId} did some work`,
    git_repo: 'masterwork',
    cwd: '/Users/dev/Projects/masterwork',
    status: 'success',
    started_at: '2026-08-09T13:00:00.000Z',
    uses,
    first_used_at: '2026-08-09T13:01:00.000Z',
    last_used_at: '2026-08-09T13:02:00.000Z',
    calls: [],
  });
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
    const inspected = new URL(url).searchParams.get('include_inspection') === 'true';
    const sessions = url.includes('/sessions');
    const body = sessions
      ? inspected
        ? [use('chat-run', 3), use('masterwork-analysis', 1)]
        : [use('chat-run', 3)]
      : inspected
        ? inspectionRows()
        : assetUsageRows();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: CORS,
      body: JSON.stringify(body),
    });
  });

  await mount(
    <TestProviders>
      <AssetScopeHarness assetId="claude:skill:frontend-dev" />
    </TestProviders>,
  );

  // The log counts its runs on the header, without being opened.
  await expect(page.getByRole('button', { name: /Used by/ })).toContainText('1 run');

  await page.getByRole('button', { name: 'Include inspection runs' }).click();

  // One toggle, both requests: the drill-in can show every run the table counted.
  await expect(page.getByRole('button', { name: /Used by/ })).toContainText('2 runs');
  await expect
    .poll(() => urls.filter((u) => u.includes('include_inspection=true')).length)
    .toBeGreaterThan(1);
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
