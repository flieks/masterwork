import { test, expect, type Page, type Route } from '@playwright/experimental-ct-react';
import type { AssetSummary, SkillMatchResponse } from '~/api/generated';
import { AssetListPage } from '~/features/assets/components/AssetListPage';
import { TestProviders } from './harness/TestProviders';
import { skillMatchResponse } from './harness/catalogFixtures';

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
    id: 'claude:skill:frontend-dev',
    kind: 'skill',
    provider: 'claude',
    name: 'frontend-dev',
    title: 'Frontend Dev',
    description: 'React frontend guidelines.',
    model: null,
    agents: ['claude'],
    path: '/Users/me/.claude/skills/frontend-dev/SKILL.md',
    created_at: '2026-08-01T10:00:00Z',
    updated_at: '2026-08-02T10:00:00Z',
    read_only: false,
    disabled: false,
    ...over,
  };
}

const INSTALLED: AssetSummary[] = [
  summary({}),
  summary({
    id: 'claude:skill:backend-dev',
    name: 'backend-dev',
    title: 'Backend Dev',
    description: 'Python FastAPI guidelines.',
  }),
];

async function mockSkills(
  page: Page,
  match: SkillMatchResponse = skillMatchResponse(),
): Promise<string[]> {
  const calls: string[] = [];
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const path = decodeURIComponent(new URL(request.url()).pathname);
    calls.push(`${request.method()} ${path}`);
    if (path === '/api/v1/assets') return json(route, INSTALLED);
    if (path === '/api/v1/skills/installed/match') return json(route, match);
    return json(route, []);
  });
  return calls;
}

test('Find by description opens the panel; typing alone fires no request', async ({
  mount,
  page,
}) => {
  const calls = await mockSkills(page);
  const component = await mount(
    <TestProviders initialEntries={['/skills']}>
      <AssetListPage kind="skill" />
    </TestProviders>,
  );

  await expect(component.getByLabel('Describe what you need')).toHaveCount(0);
  await component.getByRole('button', { name: 'Find by description' }).click();
  const textarea = component.getByLabel('Describe what you need');
  await expect(textarea).toBeVisible();

  await textarea.fill('I need to build a React feature');
  expect(calls.filter((c) => c === 'POST /api/v1/skills/installed/match')).toHaveLength(0);
});

test('clicking Find posts exactly once and renders the matched cards with their reasons', async ({
  mount,
  page,
}) => {
  const calls = await mockSkills(page);
  const component = await mount(
    <TestProviders initialEntries={['/skills']}>
      <AssetListPage kind="skill" />
    </TestProviders>,
  );

  await component.getByRole('button', { name: 'Find by description' }).click();
  await component.getByLabel('Describe what you need').fill('I need to build a React feature');
  await component.getByRole('button', { name: 'Find', exact: true }).click();

  await expect(component.getByText('Matches React frontend work.')).toBeVisible();
  await expect(component.getByText('Frontend Dev')).toBeVisible();
  await expect(component.getByText('Backend Dev')).toHaveCount(0);
  expect(calls.filter((c) => c === 'POST /api/v1/skills/installed/match')).toHaveLength(1);
});

test('Clear restores the full installed list', async ({ mount, page }) => {
  await mockSkills(page);
  const component = await mount(
    <TestProviders initialEntries={['/skills']}>
      <AssetListPage kind="skill" />
    </TestProviders>,
  );

  await component.getByRole('button', { name: 'Find by description' }).click();
  await component.getByLabel('Describe what you need').fill('React work');
  await component.getByRole('button', { name: 'Find', exact: true }).click();
  await expect(component.getByText('Backend Dev')).toHaveCount(0);

  await component.getByRole('button', { name: 'Clear' }).click();
  await expect(component.getByText('Backend Dev')).toBeVisible();
  await expect(component.getByText('Frontend Dev')).toBeVisible();
});
