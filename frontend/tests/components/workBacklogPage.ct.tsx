import { test, expect, type Page } from '@playwright/experimental-ct-react';
import type { WorkItem, WorkSource } from '~/api/generated';
import { WorkBacklogPage } from '~/features/work/components/WorkBacklogPage';
import { TestProviders } from './harness/TestProviders';
import { SOURCE_ID, startResponse, workItem, workSource } from './harness/workFixtures';

// The generated client targets a cross-origin backend (localhost:8008), so every
// fulfilled response needs CORS headers and OPTIONS preflights must be answered.
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

interface WorkRoutes {
  sources: () => WorkSource[];
  items: () => WorkItem[];
  /** Every non-preflight request, as "METHOD path". */
  calls: string[];
  bodies: string[];
}

async function mockWork(
  page: Page,
  initial: { sources?: WorkSource[]; items?: WorkItem[] } = {},
): Promise<WorkRoutes> {
  let sources = initial.sources ?? [workSource()];
  let items = initial.items ?? [];
  const calls: string[] = [];
  const bodies: string[] = [];

  await page.route('**/api/v1/work/**', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const path = new URL(request.url()).pathname;
    calls.push(`${request.method()} ${path}`);

    let body: unknown;
    if (path.endsWith('/start')) {
      body = startResponse();
    } else if (path.endsWith('/sync')) {
      // A sync brings the backlog in; the page has to go and ask again for it.
      items = [...items, workItem({ id: 99, external_id: 5000, title: 'Freshly synced story' })];
      body = { fetched: 1, inserted: 1, updated: 0 };
    } else if (path.endsWith('/work/items')) {
      body = items;
    } else if (request.method() === 'POST') {
      bodies.push(request.postData() ?? '');
      const created = workSource();
      sources = [created];
      body = created;
    } else {
      body = sources;
    }

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: CORS,
      body: JSON.stringify(body),
    });
  });

  return { sources: () => sources, items: () => items, calls, bodies };
}

test('a work item shows its type, state, iteration and priority', async ({ mount, page }) => {
  await mockWork(page, {
    items: [
      workItem(),
      workItem({
        id: 2,
        external_id: 4822,
        item_type: 'Bug',
        state: 'New',
        title: 'Retry drops the run id',
        iteration: null,
        priority: null,
      }),
    ],
  });

  await mount(
    <TestProviders>
      <WorkBacklogPage />
    </TestProviders>,
  );

  await expect(page.getByRole('heading', { name: 'Work' })).toBeVisible();
  await expect(page.getByLabel('2 work items')).toBeVisible();
  await expect(page.getByText('acme / widgets')).toBeVisible();

  const story = page
    .getByRole('row')
    .filter({ hasText: 'Operators can retry a failed ingest run' });
  await expect(story).toContainText('User Story');
  await expect(story).toContainText('Active');
  await expect(story).toContainText('widgets\\Sprint 14');
  await expect(story).toContainText('#4821');
  await expect(story).toContainText('2');

  // A bug with no iteration or priority says so rather than showing a blank cell.
  const bug = page.getByRole('row').filter({ hasText: 'Retry drops the run id' });
  await expect(bug).toContainText('Bug');
  await expect(bug).toContainText('New');
  await expect(bug.getByText('—')).toHaveCount(2);
});

test('starting an item shows the assembled prompt and says launch is deferred', async ({
  mount,
  page,
}) => {
  const routes = await mockWork(page, { items: [workItem()] });

  await mount(
    <TestProviders>
      <WorkBacklogPage />
    </TestProviders>,
  );

  await page
    .getByRole('row')
    .filter({ hasText: 'Operators can retry a failed ingest run' })
    .getByRole('button', { name: 'Start session' })
    .click();

  await expect(page.getByRole('dialog')).toContainText('Session prompt for #4821');
  await expect(page.getByText('Work this item to completion.')).toBeVisible();
  await expect(page.getByText(/Launch is deferred/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Copy prompt' })).toBeVisible();

  expect(routes.calls).toContain('POST /api/v1/work/items/1/start');
});

test('Sync pulls the source and the item list is asked for again', async ({ mount, page }) => {
  const routes = await mockWork(page, { items: [workItem()] });

  await mount(
    <TestProviders>
      <WorkBacklogPage />
    </TestProviders>,
  );

  await expect(page.getByRole('row')).toHaveCount(2); // header + one item

  await page.getByRole('button', { name: /^Sync / }).click();

  await expect(page.getByText('Freshly synced story')).toBeVisible();
  expect(routes.calls).toContain(`POST /api/v1/work/sources/${SOURCE_ID}/sync`);
  expect(routes.calls.filter((c) => c === 'GET /api/v1/work/items').length).toBeGreaterThan(1);
});

test('with no source registered the page offers the inline form', async ({ mount, page }) => {
  const routes = await mockWork(page, { sources: [] });

  await mount(
    <TestProviders>
      <WorkBacklogPage />
    </TestProviders>,
  );

  await expect(page.getByText('No work source yet')).toBeVisible();

  const add = page.getByRole('button', { name: 'Add work source' });
  await expect(add).toBeDisabled();

  await page.getByLabel('Organisation URL').fill('https://dev.azure.com/acme');
  await page.getByLabel('Project').fill('widgets');
  // The PAT env var is prefilled — the token itself never reaches the frontend.
  await expect(page.getByLabel('PAT env var')).toHaveValue('AZURE_DEVOPS_PAT');
  await expect(add).toBeEnabled();
  await add.click();

  await expect(page.getByText('acme / widgets')).toBeVisible();
  await expect(page.getByText('No work source yet')).toHaveCount(0);
  expect(JSON.parse(routes.bodies[0])).toMatchObject({
    org_url: 'https://dev.azure.com/acme',
    project: 'widgets',
    secret_ref: 'AZURE_DEVOPS_PAT',
  });
});
