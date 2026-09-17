import { test, expect, type Page, type Route } from '@playwright/experimental-ct-react';
import type { AssetSummary } from '~/api/generated';
import { Toaster } from '~/components/ui/sonner';
import { AssetListPage } from '~/features/assets/components/AssetListPage';
import { hideMergedTwins } from '~/features/assets/duplicates';
import { TestProviders } from './harness/TestProviders';

/**
 * A skill installed per agent folder AND into ~/.agents/skills: the agent copy
 * carries the twin badge and a Merge button, and the generic copy is hidden only
 * when its twins already cover every agent that loads it. Since v1.50 Codex
 * loads ~/.agents/skills natively, so an enabled generic copy lists "codex".
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
    // Each agent loads its own folder; Codex also loads the generic one natively.
    agents: provider === 'claude' ? ['claude'] : ['codex'],
    path: `/Users/me/.${provider === 'generic' ? 'agents' : provider}/skills/${name}/SKILL.md`,
    created_at: '2026-08-01T10:00:00Z',
    updated_at: '2026-08-02T10:00:00Z',
    read_only: false,
    disabled: false,
    generic_twin: null,
    ...over,
  };
}

const INSTALLED: AssetSummary[] = [
  // A Codex twin: Codex loads both copies, and the twin row already says so.
  summary({ name: 'caveman', provider: 'codex', generic_twin: 'identical' }),
  summary({ name: 'caveman', provider: 'generic' }),
  summary({ name: 'tdd', provider: 'codex', generic_twin: 'differs' }),
  summary({ name: 'tdd', provider: 'generic' }),
  // A Claude twin: Codex loads only the generic copy, so that row is real and stays.
  summary({ name: 'grilling', generic_twin: 'identical' }),
  summary({ name: 'grilling', provider: 'generic' }),
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
      const [provider, , name] = path.split('/')[4].split(':');
      return json(route, {
        asset: {
          ...summary({ name, provider: 'generic', agents: ['claude', 'codex'] }),
          content: '',
        },
        previous_id: `${provider}:skill:${name}`,
        // v1.50: only link-holding folders — Codex loads the generic copy without one.
        linked_agents: ['claude'],
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

test('hideMergedTwins hides a generic copy only when twins cover everyone who loads it', () => {
  const ids = hideMergedTwins(INSTALLED).map((a) => a.id);
  expect(ids).toEqual([
    'codex:skill:caveman',
    'codex:skill:tdd',
    'claude:skill:grilling',
    'generic:skill:grilling',
  ]);

  const edgeCases = [
    // Twins in both agent folders: nothing left for the generic row to say.
    summary({ name: 'vim', generic_twin: 'identical' }),
    summary({ name: 'vim', provider: 'codex', generic_twin: 'identical' }),
    summary({ name: 'vim', provider: 'generic' }),
    // Loaded by nobody (config.toml switched it off for Codex, no Claude link).
    summary({ name: 'orphan', generic_twin: 'differs' }),
    summary({ name: 'orphan', provider: 'generic', agents: [] }),
    // Claude links to the generic copy while a Codex twin exists: Claude's only copy stays.
    summary({ name: 'linked', provider: 'codex', generic_twin: 'identical' }),
    summary({ name: 'linked', provider: 'generic', agents: ['claude', 'codex'] }),
    // No twin at all: a generic skill is never hidden.
    summary({ name: 'solo', provider: 'generic' }),
  ];
  expect(hideMergedTwins(edgeCases).map((a) => a.id)).toEqual([
    'claude:skill:vim',
    'codex:skill:vim',
    'claude:skill:orphan',
    'codex:skill:linked',
    'generic:skill:linked',
    'generic:skill:solo',
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
      <Toaster />
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
        path: '/api/v1/assets/codex:skill:caveman/migrate',
        body: JSON.stringify({ replace_generic: false }),
      },
    ]);
  // Who loads it comes from the asset, not linked_agents, which never names Codex.
  await expect(
    page.getByText('caveman is on disk once, in ~/.agents/skills; Claude Code and Codex load it.'),
  ).toBeVisible();
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
