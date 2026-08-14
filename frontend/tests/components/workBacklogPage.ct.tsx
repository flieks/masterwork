import { test, expect, type Page } from '@playwright/experimental-ct-react';
import type { WorkItem, WorkSource } from '~/api/generated';
import { WorkBacklogPage } from '~/features/work/components/WorkBacklogPage';
import { TestProviders } from './harness/TestProviders';
import {
  SOURCE_ID,
  SPRINT_33,
  SPRINT_34,
  startResponse,
  workItem,
  workItemTree,
  workSource,
} from './harness/workFixtures';

// The generated client targets a cross-origin backend (localhost:8008), so every
// fulfilled response needs CORS headers and OPTIONS preflights must be answered.
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

const STORY = 'Operators can retry a failed ingest run';
const CHILD_WIRE = 'Wire the retry button to the API';
const CHILD_E2E = 'Cover retry in the run E2E';
const CONTEXT_FEATURE = 'Ingest reliability';
const CONTEXT_CHILD = 'Backfill the ingest audit log';
const ORPHAN = 'Bump the ingest SDK';

interface WorkRoutes {
  /** Every non-preflight request, as "METHOD path". */
  calls: string[];
  bodies: string[];
}

async function mockWork(
  page: Page,
  initial: { sources?: WorkSource[]; items?: WorkItem[] } = {},
): Promise<WorkRoutes> {
  let sources = initial.sources ?? [workSource()];
  let items = initial.items ?? workItemTree();
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

  return { calls, bodies };
}

function mountPage(mount: Parameters<Parameters<typeof test>[1]>[0]['mount']) {
  return mount(
    <TestProviders>
      <WorkBacklogPage />
    </TestProviders>,
  );
}

test('the backlog opens on its top-level rows, with children folded away', async ({
  mount,
  page,
}) => {
  await mockWork(page);
  await mountPage(mount);

  await expect(page.getByRole('heading', { name: 'Work' })).toBeVisible();
  await expect(page.getByLabel('6 work items')).toBeVisible();
  await expect(page.getByText('acme / widgets')).toBeVisible();

  // Header + story + context feature + orphan task. The three children are folded.
  await expect(page.getByRole('row')).toHaveCount(4);
  await expect(page.getByText(CHILD_WIRE)).toHaveCount(0);
  await expect(page.getByText(CONTEXT_CHILD)).toHaveCount(0);

  const story = page.getByRole('row').filter({ hasText: STORY });
  await expect(story).toContainText('User Story');
  await expect(story).toContainText('Active');
  await expect(story).toContainText('2026 Q3.3');
  await expect(story).toContainText('#4821');
  await expect(story).toContainText('2');

  // A task whose parent was never fetched still gets a row of its own.
  await expect(page.getByRole('row').filter({ hasText: ORPHAN })).toBeVisible();

  // The context Feature has no iteration and no priority — both say so.
  const feature = page.getByRole('row').filter({ hasText: CONTEXT_FEATURE });
  await expect(feature.getByText('—')).toHaveCount(2);
});

test('expanding a story reveals the tasks under it', async ({ mount, page }) => {
  await mockWork(page);
  await mountPage(mount);

  const chevron = page.getByRole('button', { name: `Expand ${STORY}` });
  await expect(chevron).toHaveAttribute('aria-expanded', 'false');
  await chevron.click();

  await expect(page.getByText(CHILD_WIRE)).toBeVisible();
  await expect(page.getByText(CHILD_E2E)).toBeVisible();
  await expect(page.getByRole('row')).toHaveCount(6);

  // Only that story opened — the context Feature's child stays folded.
  await expect(page.getByText(CONTEXT_CHILD)).toHaveCount(0);

  await page.getByRole('button', { name: `Collapse ${STORY}` }).click();
  await expect(page.getByText(CHILD_WIRE)).toHaveCount(0);
});

test('the sprint filter drops the items in other iterations', async ({ mount, page }) => {
  await mockWork(page);
  await mountPage(mount);

  await page.getByLabel('Sprint').selectOption(SPRINT_33);

  // A filtered view opens every group, so a matching child is never hidden.
  await expect(page.getByText(STORY)).toBeVisible();
  await expect(page.getByText(CHILD_WIRE)).toBeVisible();
  await expect(page.getByText(CHILD_E2E)).toBeVisible();
  await expect(page.getByLabel('3 work items')).toBeVisible();

  await expect(page.getByText(CONTEXT_FEATURE)).toHaveCount(0);
  await expect(page.getByText(CONTEXT_CHILD)).toHaveCount(0);
  await expect(page.getByText(ORPHAN)).toHaveCount(0);

  // The other sprint keeps the context Feature only as its child's group header.
  await page.getByLabel('Sprint').selectOption(SPRINT_34);
  await expect(page.getByText(CONTEXT_FEATURE)).toBeVisible();
  await expect(page.getByText(CONTEXT_CHILD)).toBeVisible();
  await expect(page.getByText(ORPHAN)).toBeVisible();
  await expect(page.getByText(STORY)).toHaveCount(0);
});

test('searching a task title keeps its parent as the group header, expanded', async ({
  mount,
  page,
}) => {
  await mockWork(page);
  await mountPage(mount);

  await page.getByLabel('Search titles').fill('cover retry');

  // The story does not match "cover retry" itself; it survives as the header.
  await expect(page.getByText(STORY)).toBeVisible();
  await expect(page.getByText(CHILD_E2E)).toBeVisible();
  await expect(page.getByText(CHILD_WIRE)).toHaveCount(0);
  await expect(page.getByText(ORPHAN)).toHaveCount(0);
  // The chevron still answers while a filter holds the group open.
  await page.getByRole('button', { name: `Collapse ${STORY}` }).click();
  await expect(page.getByText(CHILD_E2E)).toHaveCount(0);
  await page.getByRole('button', { name: `Expand ${STORY}` }).click();
  await expect(page.getByText(CHILD_E2E)).toBeVisible();

  await page.getByLabel('Search titles').fill('nothing matches this');
  await expect(page.getByText('No work item matches these filters')).toBeVisible();
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await expect(page.getByRole('row')).toHaveCount(4);
});

test('clicking a title opens the item, description and acceptance as plain text', async ({
  mount,
  page,
}) => {
  await mockWork(page);
  await mountPage(mount);

  await page.getByRole('button', { name: STORY, exact: true }).click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText(STORY);
  await expect(dialog).toContainText('#4821');
  await expect(dialog).toContainText('The retry button reruns the pipeline from the failed stage.');
  await expect(dialog).toContainText('- Retry is disabled while a run is in flight');
  await expect(dialog).toContainText('User Story');
  await expect(dialog).toContainText(SPRINT_33);
  await expect(dialog).toContainText('ingest');
  await expect(dialog.getByRole('link', { name: /Open in Azure DevOps/ })).toHaveAttribute(
    'href',
    'https://dev.azure.com/acme/widgets/_workitems/edit/4821',
  );
});

test('a context row is startable from nowhere — not the row, not its dialog', async ({
  mount,
  page,
}) => {
  await mockWork(page);
  await mountPage(mount);

  const feature = page.getByRole('row').filter({ hasText: CONTEXT_FEATURE });
  await expect(feature.getByText('context')).toBeVisible();
  await expect(feature.getByRole('button', { name: 'Start session' })).toHaveCount(0);

  // The title still opens the item — it is context, not a forbidden row.
  await page.getByRole('button', { name: CONTEXT_FEATURE, exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('Umbrella for the 2026 Q3 ingest hardening work.');
  await expect(dialog.getByRole('button', { name: 'Start session' })).toHaveCount(0);
});

test('starting an item shows the assembled prompt and says launch is deferred', async ({
  mount,
  page,
}) => {
  const routes = await mockWork(page, { items: [workItem()] });
  await mountPage(mount);

  await page
    .getByRole('row')
    .filter({ hasText: STORY })
    .getByRole('button', { name: 'Start session' })
    .click();

  await expect(page.getByRole('dialog')).toContainText('Session prompt for #4821');
  await expect(page.getByText('Work this item to completion.')).toBeVisible();
  await expect(page.getByText(/Launch is deferred/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Copy prompt' })).toBeVisible();

  expect(routes.calls).toContain('POST /api/v1/work/items/1/start');
});

test('the detail dialog hands straight over to the prompt', async ({ mount, page }) => {
  await mockWork(page, { items: [workItem()] });
  await mountPage(mount);

  await page.getByRole('button', { name: STORY, exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Start session' }).click();

  // One modal at a time: the detail closes as the prompt opens.
  await expect(page.getByRole('dialog')).toHaveCount(1);
  await expect(page.getByRole('dialog')).toContainText('Session prompt for #4821');
});

test('Sync pulls the source and the item list is asked for again', async ({ mount, page }) => {
  const routes = await mockWork(page, { items: [workItem()] });
  await mountPage(mount);

  await expect(page.getByRole('row')).toHaveCount(2); // header + one item

  await page.getByRole('button', { name: /^Sync / }).click();

  await expect(page.getByText('Freshly synced story')).toBeVisible();
  expect(routes.calls).toContain(`POST /api/v1/work/sources/${SOURCE_ID}/sync`);
  expect(routes.calls.filter((c) => c === 'GET /api/v1/work/items').length).toBeGreaterThan(1);
});

test('with no source registered the page offers the inline form', async ({ mount, page }) => {
  const routes = await mockWork(page, { sources: [] });
  await mountPage(mount);

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
