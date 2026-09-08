import { test, expect, type Page, type Route } from '@playwright/experimental-ct-react';
import type { AssetDetail, AssetMigrationResult, AssetSummary } from '~/api/generated';
import { AssetListPage } from '~/features/assets/components/AssetListPage';
import { TestProviders } from './harness/TestProviders';
import { SkillDetailApp } from './harness/SkillDetailApp';

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
  return {
    id: 'claude:skill:tdd',
    kind: 'skill',
    provider: 'claude',
    name: 'tdd',
    title: 'tdd',
    description: 'Test first.',
    model: null,
    agents: ['claude'],
    path: '/Users/me/.claude/skills/tdd/SKILL.md',
    created_at: '2026-08-01T10:00:00Z',
    updated_at: '2026-08-02T10:00:00Z',
    read_only: false,
    disabled: false,
    ...over,
  };
}

function detail(over: Partial<AssetDetail>): AssetDetail {
  return { ...summary({}), content: '---\nname: tdd\n---\n# TDD\n', ...over };
}

const CLAUDE = detail({});
const GENERIC = detail({
  id: 'generic:skill:tdd',
  provider: 'generic',
  agents: ['claude', 'codex'],
  path: '/Users/me/.agents/skills/tdd/SKILL.md',
});

async function mockAssets(
  page: Page,
  opts: {
    list?: AssetSummary[];
    migrate?: AssetMigrationResult;
    migrateStatus?: number;
    /** 409 "differs" until the request carries replace_generic, then succeed. */
    conflictUntilReplace?: boolean;
  } = {},
) {
  const calls: string[] = [];
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const path = decodeURIComponent(new URL(request.url()).pathname);
    calls.push(`${request.method()} ${path}`);
    if (path === '/api/v1/assets') return json(route, opts.list ?? []);
    if (path === '/api/v1/assets/claude:skill:tdd/migrate') {
      const body = request.postDataJSON() as { replace_generic?: boolean } | null;
      calls.push(`replace_generic=${String(body?.replace_generic ?? false)}`);
      if (opts.conflictUntilReplace && !body?.replace_generic) {
        return json(route, { detail: "~/.agents/skills already holds a 'tdd' that differs" }, 409);
      }
      if (opts.migrateStatus && opts.migrateStatus >= 400) {
        return json(route, { detail: 'the generic folder already holds tdd' }, opts.migrateStatus);
      }
      return json(route, opts.migrate);
    }
    if (path === '/api/v1/assets/claude:skill:tdd') return json(route, CLAUDE);
    if (path === '/api/v1/assets/generic:skill:tdd') return json(route, GENERIC);
    if (path.startsWith('/api/v1/assets/') && path.endsWith('/diagram')) {
      return json(route, { detail: 'not found' }, 404);
    }
    if (path.startsWith('/api/v1/coding/')) return json(route, []);
    return json(route, []);
  });
  return calls;
}

test.describe('skills: which agents load them', () => {
  test('the list badges a Claude-only, a Codex-only and a generic skill', async ({
    mount,
    page,
  }) => {
    await mockAssets(page, {
      list: [
        summary({}),
        summary({
          id: 'codex:skill:theirs',
          name: 'theirs',
          title: 'theirs',
          provider: 'codex',
          agents: ['codex'],
        }),
        summary({
          id: 'generic:skill:shared',
          name: 'shared',
          title: 'shared',
          provider: 'generic',
          agents: ['claude', 'codex'],
        }),
        summary({
          id: 'generic:skill:orphan',
          name: 'orphan',
          title: 'orphan',
          provider: 'generic',
          agents: [],
        }),
      ],
    });
    const component = await mount(
      <TestProviders initialEntries={['/skills']}>
        <AssetListPage kind="skill" />
      </TestProviders>,
    );

    await expect(component.getByText('Claude only')).toBeVisible();
    await expect(component.getByText('Codex only')).toBeVisible();
    await expect(component.getByText('Generic · Claude, Codex')).toBeVisible();
    await expect(component.getByText('Generic · unlinked')).toBeVisible();
  });

  test('a Claude skill can be made generic and lands on its new id', async ({ mount, page }) => {
    const calls = await mockAssets(page, {
      migrate: {
        asset: GENERIC,
        previous_id: 'claude:skill:tdd',
        linked_agents: ['claude', 'codex'],
        skipped_agents: [],
        claude_only_keys: ['disable-model-invocation'],
        name_rewritten: false,
        relinked_projects: 1,
        adopted: false,
        replaced_generic: false,
      },
    });
    const component = await mount(<SkillDetailApp path="/skills/tdd" />);

    await expect(component.getByText('Claude only')).toBeVisible();
    await component.getByRole('button', { name: 'Make generic' }).click();

    const dialog = page.getByRole('alertdialog');
    await expect(dialog).toContainText('~/.agents/skills/tdd');
    await dialog.getByRole('button', { name: 'Make generic' }).click();

    await expect(page.getByText('Made generic')).toBeVisible();
    await expect(page.getByText(/Kept Claude-only keys: disable-model-invocation/)).toBeVisible();
    // Now on the generic id: the badge flips and the button is gone.
    await expect(component.getByText('Generic · Claude, Codex')).toBeVisible();
    await expect(component.getByRole('button', { name: 'Make generic' })).toHaveCount(0);
    expect(calls).toContain('POST /api/v1/assets/claude:skill:tdd/migrate');
    expect(calls).toContain('GET /api/v1/assets/generic:skill:tdd');
  });

  test('a differing generic copy re-arms the dialog into replacing it', async ({ mount, page }) => {
    const calls = await mockAssets(page, {
      conflictUntilReplace: true,
      migrate: {
        asset: GENERIC,
        previous_id: 'claude:skill:tdd',
        linked_agents: ['claude'],
        skipped_agents: [],
        claude_only_keys: [],
        name_rewritten: false,
        relinked_projects: 0,
        adopted: false,
        replaced_generic: true,
      },
    });
    const component = await mount(<SkillDetailApp path="/skills/tdd" />);

    await component.getByRole('button', { name: 'Make generic' }).click();
    const dialog = page.getByRole('alertdialog');
    await dialog.getByRole('button', { name: 'Make generic' }).click();

    // First press never replaces: the dialog stays open, now naming the loss.
    await expect(dialog).toContainText('already exists and differs');
    await expect(page.getByText("Couldn't make it generic")).toHaveCount(0);
    await dialog.getByRole('button', { name: 'Replace and make generic' }).click();

    await expect(page.getByText('Made generic')).toBeVisible();
    await expect(component.getByText('Generic · Claude, Codex')).toBeVisible();
    expect(calls.filter((c) => c.startsWith('replace_generic='))).toEqual([
      'replace_generic=false',
      'replace_generic=true',
    ]);
  });

  test('a refused move keeps the page where it was', async ({ mount, page }) => {
    await mockAssets(page, { migrateStatus: 409 });
    const component = await mount(<SkillDetailApp path="/skills/tdd" />);

    await component.getByRole('button', { name: 'Make generic' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Make generic' }).click();

    await expect(page.getByText("Couldn't make it generic")).toBeVisible();
    await expect(component.getByText('Claude only')).toBeVisible();
    await expect(component.getByRole('button', { name: 'Make generic' })).toBeVisible();
  });

  test('a generic skill shows its home and offers no move', async ({ mount, page }) => {
    await mockAssets(page);
    const component = await mount(<SkillDetailApp path="/skills/tdd?p=generic" />);

    await expect(component.getByText('Generic · Claude, Codex')).toBeVisible();
    await expect(component.getByText('generic', { exact: true })).toBeVisible();
    await expect(component.getByRole('button', { name: 'Make generic' })).toHaveCount(0);
  });
});
