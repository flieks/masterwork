import { test, expect, type Page, type Route } from '@playwright/experimental-ct-react';
import type { AssetSummary } from '~/api/generated';
import { AssetListPage } from '~/features/assets/components/AssetListPage';
import { hideMergedTwins } from '~/features/assets/duplicates';
import { TestProviders } from './harness/TestProviders';

/**
 * A skill installed per agent folder AND into ~/.agents/skills is one skill,
 * not two rows: the agent copy carries the twin badge and a Merge button, the
 * orphaned generic copy is hidden.
 */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    headers: CORS,
    body: JSON.stringify(body),
  });
}

function summary(over: Partial<AssetSummary>): AssetSummary {
  const name = over.name ?? 'caveman';
  const provider = over.provider ?? 'claude';
  return {
    id: `${provider}:skill:${name}`,
    kind: 'skill',
    provider,
    name,
    title: name,
    description: 'Ultra-compressed communication.',
    model: null,
    agents: provider === 'claude' ? ['claude'] : [],
    path: `/Users/me/.${provider}/skills/${name}/SKILL.md`,
    created_at: '2026-08-01T10:00:00Z',
    updated_at: '2026-08-02T10:00:00Z',
    read_only: false,
    disabled: false,
    generic_twin: null,
    ...over,
  };
}

const INSTALLED: AssetSummary[] = [
  summary({ name: 'caveman', generic_twin: 'identical' }),
  summary({ name: 'caveman', provider: 'generic' }),
  summary({ name: 'tdd', generic_twin: 'differs' }),
  summary({ name: 'tdd', provider: 'generic' }),
  // Linked into Codex: a real row in its own right, never hidden.
  summary({ name: 'grilling', generic_twin: 'identical' }),
  summary({ name: 'grilling', provider: 'generic', agents: ['codex'] }),
];

async function mockAssets(page: Page): Promise<{ method: string; path: string; body: string }[]> {
  const calls: { method: string; path: string; body: string }[] = [];
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const path = decodeURIComponent(new URL(request.url()).pathname);
    calls.push({ method: request.method(), path, body: request.postData() ?? '' });
    if (path === '/api/v1/assets') return json(route, INSTALLED);
    if (path.endsWith('/migrate')) {
      const name = path.split('/')[4].split(':')[2];
      return json(route, {
        asset: {
          ...summary({ name, provider: 'generic', agents: ['claude', 'codex'] }),
          content: '',
        },
        previous_id: `claude:skill:${name}`,
        linked_agents: ['claude', 'codex'],
        skipped_agents: [],
        claude_only_keys: [],
        name_rewritten: false,
        relinked_projects: 0,
        adopted: name === 'caveman',
        replaced_generic: name === 'tdd',
      });
    }
    return json(route, []);
  });
  return calls;
}

test('hideMergedTwins keeps one row per twinned skill and every linked generic', () => {
  const ids = hideMergedTwins(INSTALLED).map((a) => a.id);
  expect(ids).toEqual([
    'claude:skill:caveman',
    'claude:skill:tdd',
    'claude:skill:grilling',
    'generic:skill:grilling',
  ]);
});

test('a twinned skill lists once, badged, with a Merge button', async ({ mount, page }) => {
  await mockAssets(page);
  await mount(
    <TestProviders initialEntries={['/skills']}>
      <AssetListPage kind="skill" />
    </TestProviders>,
  );
  await page.getByRole('button', { name: 'Table view' }).click();

  await expect(page.getByRole('link', { name: /caveman/ })).toHaveCount(1);
  await expect(page.getByRole('link', { name: /grilling/ })).toHaveCount(2);
  await expect(page.getByLabel('4 skills')).toBeVisible();
  const caveman = page.getByRole('link', { name: /caveman/ });
  await expect(caveman.getByText('Duplicate of generic copy')).toBeVisible();
  await expect(caveman.getByRole('button', { name: 'Merge' })).toBeVisible();
  await expect(
    page.getByRole('link', { name: /tdd/ }).getByText('Differs from generic copy'),
  ).toBeVisible();
});

test('Merge on an identical twin migrates at once without leaving the list', async ({
  mount,
  page,
}) => {
  const calls = await mockAssets(page);
  await mount(
    <TestProviders initialEntries={['/skills']}>
      <AssetListPage kind="skill" />
    </TestProviders>,
  );
  await page.getByRole('button', { name: 'Table view' }).click();

  await page
    .getByRole('link', { name: /caveman/ })
    .getByRole('button', { name: 'Merge' })
    .click();

  await expect
    .poll(() => calls.filter((c) => c.method === 'POST' && c.path.endsWith('/migrate')))
    .toEqual([
      {
        method: 'POST',
        path: '/api/v1/assets/claude:skill:caveman/migrate',
        body: JSON.stringify({ replace_generic: false }),
      },
    ]);
  // The row click did not fire: still on the list.
  await expect(page.getByRole('heading', { name: 'Skills' })).toBeVisible();
});

test('Merge on a differing twin asks before replacing the generic copy', async ({
  mount,
  page,
}) => {
  const calls = await mockAssets(page);
  await mount(
    <TestProviders initialEntries={['/skills']}>
      <AssetListPage kind="skill" />
    </TestProviders>,
  );
  await page.getByRole('button', { name: 'Table view' }).click();

  await page.getByRole('link', { name: /tdd/ }).getByRole('button', { name: 'Merge' }).click();
  const dialog = page.getByRole('alertdialog');
  await expect(dialog).toContainText('Replace the generic copy of “tdd”?');
  expect(calls.filter((c) => c.method === 'POST')).toEqual([]);

  await dialog.getByRole('button', { name: 'Replace and make generic' }).click();
  await expect
    .poll(() => calls.filter((c) => c.method === 'POST').map((c) => c.body))
    .toEqual([JSON.stringify({ replace_generic: true })]);
});
