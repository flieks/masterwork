import { test, expect, type Page, type Route } from '@playwright/experimental-ct-react';
import type { AssetDetail, AssetSummary } from '~/api/generated';
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

const ENABLED: AssetDetail = { ...summary({}), content: '---\nname: tdd\n---\n# TDD\n' };
// Same id, one level deeper on disk, loaded by nobody.
const DISABLED: AssetDetail = {
  ...ENABLED,
  disabled: true,
  agents: [],
  path: '/Users/me/.claude/skills/.disabled/tdd/SKILL.md',
};

const PLUGIN: AssetDetail = {
  ...ENABLED,
  id: 'claude-plugin:skill:vercel:deploy',
  provider: 'claude-plugin',
  name: 'vercel:deploy',
  title: 'vercel:deploy',
  read_only: true,
};

async function mockAssets(page: Page, opts: { list?: AssetSummary[]; toggleStatus?: number } = {}) {
  const calls: string[] = [];
  // The detail the page reads follows the last toggle, like the real backend.
  let current = ENABLED;
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const path = decodeURIComponent(new URL(request.url()).pathname);
    calls.push(`${request.method()} ${path}`);
    if (path === '/api/v1/assets') return json(route, opts.list ?? []);
    if (path === '/api/v1/assets/claude:skill:tdd/enabled') {
      const body = request.postDataJSON() as { enabled: boolean };
      calls.push(`enabled=${String(body.enabled)}`);
      if (opts.toggleStatus && opts.toggleStatus >= 400) {
        return json(
          route,
          { detail: '~/.claude/skills/.disabled/tdd already exists; not overwriting it' },
          opts.toggleStatus,
        );
      }
      current = body.enabled ? ENABLED : DISABLED;
      return json(route, current);
    }
    if (path === '/api/v1/assets/claude:skill:tdd') return json(route, current);
    if (path === `/api/v1/assets/${PLUGIN.id}`) return json(route, PLUGIN);
    if (path.startsWith('/api/v1/assets/') && path.endsWith('/diagram')) {
      return json(route, { detail: 'not found' }, 404);
    }
    return json(route, []);
  });
  return calls;
}

test.describe('skills: switching one off without deleting it', () => {
  test('the switch parks the skill and brings it back, same page throughout', async ({
    mount,
    page,
  }) => {
    const calls = await mockAssets(page);
    const component = await mount(<SkillDetailApp path="/skills/tdd" />);
    const toggle = component.getByRole('switch', { name: 'Enabled' });
    const label = component.locator('label', { hasText: 'Enabled' });

    await expect(toggle).toBeChecked();
    await expect(component.getByText('Claude only')).toBeVisible();
    await label.click();

    await expect(page.getByText('Disabled', { exact: true }).first()).toBeVisible();
    await expect(page.getByText(/moved to \.disabled\//)).toBeVisible();
    await expect(toggle).not.toBeChecked();
    await expect(component.getByText('Not loaded by any agent')).toBeVisible();
    await expect(component.getByTitle(/Parked under \.disabled/)).toBeVisible();
    await expect(component.getByText('Claude only')).toHaveCount(0);
    // Still the same skill: the edit button is there, the id did not change.
    await expect(component.getByRole('button', { name: 'Edit' })).toBeVisible();

    await label.click();
    await expect(page.getByText(/loads again/)).toBeVisible();
    await expect(toggle).toBeChecked();
    await expect(component.getByText('Claude only')).toBeVisible();
    await expect(component.getByTitle(/Parked under \.disabled/)).toHaveCount(0);
    expect(calls.filter((c) => c.startsWith('enabled='))).toEqual([
      'enabled=false',
      'enabled=true',
    ]);
  });

  test('a refused toggle shows the reason and leaves the switch on', async ({ mount, page }) => {
    await mockAssets(page, { toggleStatus: 409 });
    const component = await mount(<SkillDetailApp path="/skills/tdd" />);
    const toggle = component.getByRole('switch', { name: 'Enabled' });

    await component.locator('label', { hasText: 'Enabled' }).click();

    await expect(page.getByText("Couldn't disable it")).toBeVisible();
    await expect(page.getByText(/already exists; not overwriting it/)).toBeVisible();
    await expect(toggle).toBeChecked();
    await expect(component.getByText('Claude only')).toBeVisible();
    await expect(component.getByTitle(/Parked under \.disabled/)).toHaveCount(0);
  });

  test('a plugin skill has no switch', async ({ mount, page }) => {
    await mockAssets(page);
    const component = await mount(<SkillDetailApp path="/skills/vercel:deploy?p=claude-plugin" />);

    await expect(component.getByText('Read-only')).toBeVisible();
    await expect(component.getByRole('switch')).toHaveCount(0);
  });

  test('the list mutes a disabled skill in both views but still shows it', async ({
    mount,
    page,
  }) => {
    await mockAssets(page, {
      list: [
        summary({}),
        summary({
          ...DISABLED,
          id: 'claude:skill:parked',
          name: 'parked',
          title: 'parked',
          description: 'Off for now.',
        }),
      ],
    });
    const component = await mount(
      <TestProviders initialEntries={['/skills']}>
        <AssetListPage kind="skill" />
      </TestProviders>,
    );

    for (const view of ['Grid view', 'Table view']) {
      await component.getByRole('button', { name: view }).click();
      await expect(component.getByText('parked')).toBeVisible();
      await expect(component.getByTitle(/Parked under \.disabled/)).toHaveCount(1);
      await expect(component.getByText('Not loaded by any agent')).toBeVisible();
      await expect(component.getByText('Claude only')).toBeVisible();
      // Only the parked one is faded.
      const muted = component.locator('.opacity-60');
      await expect(muted).toHaveCount(1);
      await expect(muted).toContainText('parked');
      await expect(muted).not.toContainText('tdd');
    }
  });
});
