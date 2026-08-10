import { test, expect, type Page } from '@playwright/experimental-ct-react';
import type { CodingSession } from '~/api/generated';
import { ChildRuns } from '~/features/sessions/components/ChildRuns';
import { TestProviders } from './harness/TestProviders';
import { chatRun, factoryRunSummary } from './harness/runFixtures';

/**
 * A pipeline run's stages, listed by asking the server for them.
 *
 * These tests exist because the first version asked for the unfiltered list and
 * picked its own children out: against the real DB a parent whose card read
 * "4 stage runs" expanded to none of them, because all four sat past the page
 * the client happened to get. The mock reproduces that exactly — the unscoped
 * list holds strangers only.
 */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

const PARENT_ID = 'factory-5e3b0f90';

const STAGES = ['plan', 'build', 'review', 'document'];

function stageRun(name: string, overrides: Partial<CodingSession> = {}): CodingSession {
  return factoryRunSummary({
    id: `${PARENT_ID}-${name}`,
    title: `${name} stage`,
    title_source: 'factory',
    parent_session_id: PARENT_ID,
    child_count: 0,
    launch_mode: 'automated',
    phases: [],
    ...overrides,
  });
}

/**
 * The children come back only for a request that names the parent. Anything
 * else gets a page of unrelated runs — the population the old client-side
 * filter was searching, and never found its children in.
 */
async function mockChildren(
  page: Page,
  children: CodingSession[] = STAGES.map((name) => stageRun(name)),
): Promise<{ urls: string[] }> {
  const urls: string[] = [];
  await page.route('**/api/v1/**', async (route) => {
    if (route.request().method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const url = route.request().url();
    urls.push(url);
    const scoped = new URL(url).searchParams.get('parent_session_id') === PARENT_ID;
    const strangers = [factoryRunSummary(), { ...chatRun(), phases: [] }];
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: CORS,
      body: JSON.stringify(scoped ? children : strangers),
    });
  });
  return { urls };
}

function mountChildRuns(mount: Parameters<Parameters<typeof test>[1]>[0]['mount'], count = 4) {
  return mount(
    <TestProviders>
      <ChildRuns sessionId={PARENT_ID} childCount={count} />
    </TestProviders>,
  );
}

test('the stage list asks the server for this parent, not for a page to sift', async ({
  mount,
  page,
}) => {
  const { urls } = await mockChildren(page);
  await mountChildRuns(mount);

  // Nothing is fetched until the affordance is opened.
  expect(urls).toHaveLength(0);

  await page.getByRole('button', { name: /4 stage runs/ }).click();

  await expect(page.getByRole('link')).toHaveCount(4);
  await expect(page.getByRole('link').first()).toContainText('plan stage');

  const asked = urls.at(-1) ?? '';
  expect(asked).toContain(`parent_session_id=${PARENT_ID}`);
  // The complement of the grid's scope: naming a parent already chose it.
  expect(asked).not.toContain('roots_only=true');
});

test('the expanded list matches the count on the header exactly', async ({ mount, page }) => {
  await mockChildren(page);
  await mountChildRuns(mount);

  await page.getByRole('button', { name: /4 stage runs/ }).click();

  // `child_count` counts the same population this scope returns, because the
  // backend drops `include_empty`/`include_automated` here — a headless stage
  // would fail both and the header would promise runs the list couldn't show.
  await expect(page.getByRole('listitem')).toHaveCount(4);
});

test('what the server returned is what is shown — no second guess client-side', async ({
  mount,
  page,
}) => {
  // The scope decides membership, not the row's own field. Re-filtering on
  // `parent_session_id` here would silently drop this one.
  const children = STAGES.map((name, i) =>
    stageRun(name, i === 0 ? { parent_session_id: null } : {}),
  );
  await mockChildren(page, children);
  await mountChildRuns(mount);

  await page.getByRole('button', { name: /4 stage runs/ }).click();

  await expect(page.getByRole('link')).toHaveCount(4);
});

test('a parent whose stages were never recorded says so', async ({ mount, page }) => {
  await mockChildren(page, []);
  await mountChildRuns(mount, 2);

  await page.getByRole('button', { name: /2 stage runs/ }).click();

  await expect(page.getByText('The stage runs were not recorded separately.')).toBeVisible();
});
