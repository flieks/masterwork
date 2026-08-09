import { test, expect, type Page } from '@playwright/experimental-ct-react';
import type { AssetSessionUse, AssetSummary, CodingAssetUsage } from '~/api/generated';
import { AssetListPage } from '~/features/assets/components/AssetListPage';
import { AssetUsageLog } from '~/features/assets/components/AssetUsageLog';
import { TestProviders } from './harness/TestProviders';

/**
 * What a run has actually done with a skill or an agent: the counts on the
 * overview, and the per-run log with the arguments each call carried.
 */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

function skill(name: string, title: string): AssetSummary {
  return {
    id: `claude:skill:${name}`,
    kind: 'skill',
    provider: 'claude',
    name,
    title,
    description: `What ${name} is for.`,
    model: null,
    path: `/Users/me/.claude/skills/${name}/SKILL.md`,
    updated_at: '2026-08-01T09:00:00.000Z',
    read_only: false,
  };
}

const USAGE: CodingAssetUsage[] = [
  {
    kind: 'skill',
    name: 'tdd',
    asset_id: 'claude:skill:tdd',
    sessions: 3,
    uses: 7,
    last_used_at: '2026-08-09T14:02:01.000Z',
  },
];

const SESSION_USES: AssetSessionUse[] = [
  {
    session_id: 'run-1',
    title: 'Ship the settings page',
    git_repo: 'masterwork',
    cwd: '/Users/me/Projects/masterwork',
    status: 'success',
    started_at: '2026-08-09T13:00:00.000Z',
    uses: 2,
    first_used_at: '2026-08-09T13:05:00.000Z',
    last_used_at: '2026-08-09T13:40:00.000Z',
    calls: [
      {
        used_at: '2026-08-09T13:40:00.000Z',
        lane: 'main',
        source: 'skill_call',
        input: { args: 'ultra' },
      },
      {
        used_at: '2026-08-09T13:05:00.000Z',
        lane: 'build',
        source: 'skill_read',
        input: { path: '/Users/me/.claude/skills/tdd/SKILL.md' },
      },
    ],
  },
  {
    session_id: 'run-2',
    title: null,
    git_repo: 'other-repo',
    cwd: '/Users/me/Projects/other-repo',
    status: 'abandoned',
    started_at: '2026-08-08T10:00:00.000Z',
    uses: 1,
    first_used_at: '2026-08-08T10:10:00.000Z',
    last_used_at: '2026-08-08T10:10:00.000Z',
    calls: [],
  },
];

/** Routes both reads the pages make; every response is JSON with CORS. */
async function mockApi(
  page: Page,
  { assets = [skill('tdd', 'TDD')], usage = USAGE, sessionUses = SESSION_USES } = {},
) {
  await page.route('**/api/v1/**', async (route) => {
    if (route.request().method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const url = route.request().url();
    const body = url.includes('/sessions')
      ? sessionUses
      : url.includes('/coding-assets')
        ? usage
        : assets;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: CORS,
      body: JSON.stringify(body),
    });
  });
}

// ------------------------------------------------------------- overview ---

test('a card carries the uses, the runs and when it was last used', async ({ mount, page }) => {
  await mockApi(page);
  await mount(
    <TestProviders>
      <AssetListPage kind="skill" />
    </TestProviders>,
  );

  await expect(page.getByText('7 uses · 3 runs')).toBeVisible();
  await expect(page.locator('time')).toHaveAttribute('datetime', '2026-08-09T14:02:01.000Z');
});

test('a card nothing has used says so rather than showing a zero', async ({ mount, page }) => {
  await mockApi(page, { assets: [skill('graphify', 'Graphify')] });
  await mount(
    <TestProviders>
      <AssetListPage kind="skill" />
    </TestProviders>,
  );

  await expect(page.getByText('Never used')).toBeVisible();
});

test('the table view carries the same counts as columns', async ({ mount, page }) => {
  await mockApi(page);
  await mount(
    <TestProviders>
      <AssetListPage kind="skill" />
    </TestProviders>,
  );
  await page.getByRole('button', { name: 'Table view' }).click();

  const cells = page.locator('tbody tr td');
  await expect(cells.nth(3)).toHaveText('7');
  await expect(cells.nth(4)).toHaveText('3');
});

/** The log panel is collapsed on arrival; every log assertion starts by opening it. */
async function mountLog(
  mount: (component: JSX.Element) => Promise<unknown>,
  component: JSX.Element,
  page: Page,
) {
  await mount(component);
  const panel = page.getByRole('button', { name: /Used by/ });
  await expect(panel).toHaveAttribute('aria-expanded', 'false');
  await panel.click();
}

// ------------------------------------------------------------- the log ---

test('the panel is collapsed on arrival but still says how many runs used it', async ({
  mount,
  page,
}) => {
  await mockApi(page);
  await mount(
    <TestProviders>
      <AssetUsageLog assetId="claude:skill:tdd" />
    </TestProviders>,
  );

  const panel = page.getByRole('button', { name: /Used by/ });
  await expect(panel).toHaveAttribute('aria-expanded', 'false');
  await expect(panel).toContainText('2 runs');
  await expect(page.getByRole('table')).toBeHidden();
});

test('the log lists each run that used the asset and links to it', async ({ mount, page }) => {
  await mockApi(page);
  await mountLog(
    mount,
    <TestProviders>
      <AssetUsageLog assetId="claude:skill:tdd" />
    </TestProviders>,
    page,
  );

  await expect(page.getByRole('button', { name: /Ship the settings page/ })).toBeVisible();
  // A run that never carried a prompt is named for where it ran.
  await expect(page.getByRole('button', { name: /other-repo/ })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Open run' }).first()).toHaveAttribute(
    'href',
    '/sessions/run-1',
  );
});

test('expanding a row shows the arguments each call carried, without navigating', async ({
  mount,
  page,
}) => {
  await mockApi(page);
  await mountLog(
    mount,
    <TestProviders>
      <AssetUsageLog assetId="claude:skill:tdd" />
    </TestProviders>,
    page,
  );

  const row = page.getByRole('button', { name: /Ship the settings page/ });
  await expect(row).toHaveAttribute('aria-expanded', 'false');
  await expect(page.getByText('ultra')).toBeHidden();

  await row.click();

  await expect(row).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByText('Skill call')).toBeVisible();
  await expect(page.getByText('ultra')).toBeVisible();
  // The lane that made the call is what makes this attribution.
  await expect(page.getByText('build', { exact: true })).toBeVisible();
});

test('a call with no arguments says why instead of showing an empty block', async ({
  mount,
  page,
}) => {
  await mockApi(page, {
    sessionUses: [
      {
        ...SESSION_USES[0],
        calls: [
          { used_at: '2026-08-09T13:40:00.000Z', lane: null, source: 'subagent_stop', input: null },
        ],
      },
    ],
  });
  await mountLog(
    mount,
    <TestProviders>
      <AssetUsageLog assetId="claude:agent:qa-tester" />
    </TestProviders>,
    page,
  );

  await page.getByRole('button', { name: /Ship the settings page/ }).click();
  await expect(page.getByText(/the spawn call was not recorded/)).toBeVisible();
});

test('a run recorded before the log shipped says so rather than looking unused', async ({
  mount,
  page,
}) => {
  await mockApi(page);
  await mountLog(
    mount,
    <TestProviders>
      <AssetUsageLog assetId="claude:skill:tdd" />
    </TestProviders>,
    page,
  );

  await page.getByRole('button', { name: /other-repo/ }).click();
  await expect(page.getByText(/Rebuild it from its stored events/)).toBeVisible();
});

test('an asset nothing has used shows the empty state', async ({ mount, page }) => {
  await mockApi(page, { sessionUses: [] });
  await mountLog(
    mount,
    <TestProviders>
      <AssetUsageLog assetId="claude:skill:tdd" />
    </TestProviders>,
    page,
  );

  await expect(page.getByText(/No recorded run has used this yet/)).toBeVisible();
});
