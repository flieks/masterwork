import { test, expect, type Page, type Route } from '@playwright/experimental-ct-react';
import type { PullRequestDelegateResponse, WorkPrThread, WorkPullRequest } from '~/api/generated';
import { Toaster } from '~/components/ui/sonner';
import { WorkBacklogPage } from '~/features/work/components/WorkBacklogPage';
import { TestProviders } from './harness/TestProviders';
import {
  delegateResponse,
  prComment,
  prThread,
  pullRequest,
  workSource,
} from './harness/workFixtures';

// The generated client targets a cross-origin backend (localhost:8008), so every
// fulfilled response needs CORS headers and OPTIONS preflights must be answered.
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

const PR_TITLE = 'Fix the retry button';
const REVIEW = 'This can throw on empty input.';
const OLD_REVIEW = 'Naming nit, already handled.';

function json(route: Route, body: unknown) {
  return route.fulfill({
    status: 200,
    contentType: 'application/json',
    headers: CORS,
    body: JSON.stringify(body),
  });
}

interface PrRoutes {
  /** Every non-preflight request, as "METHOD path". */
  calls: string[];
  bodies: string[];
}

/** The work + launcher endpoints the PR tab touches. `delegates` is consumed one
 * response per call, so a test can make the first delegate fail to resolve and
 * the retry after saving a folder succeed. */
async function mockPrs(
  page: Page,
  initial: {
    prs?: WorkPullRequest[];
    threads?: WorkPrThread[];
    delegates?: PullRequestDelegateResponse[];
  } = {},
): Promise<PrRoutes> {
  const prs = initial.prs ?? [pullRequest()];
  const threads = initial.threads ?? [prThread()];
  const delegates = [...(initial.delegates ?? [delegateResponse()])];
  const calls: string[] = [];
  const bodies: string[] = [];

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const path = new URL(request.url()).pathname;
    calls.push(`${request.method()} ${path}`);
    if (request.method() === 'POST') bodies.push(request.postData() ?? '');

    if (path.endsWith('/threads')) {
      await json(route, threads);
    } else if (path.endsWith('/delegate')) {
      await json(route, delegates.length > 1 ? delegates.shift() : delegates[0]);
    } else if (path.endsWith('/work/repo-paths')) {
      await json(route, { id: 1, remote_url: 'x', local_path: '/Users/dev/projects/widgets-api' });
    } else if (path.endsWith('/work/prs')) {
      await json(route, prs);
    } else if (path.endsWith('/work/items')) {
      await json(route, []);
    } else if (path.endsWith('/launcher/browse')) {
      await json(route, {
        path: '/Users/dev/projects/widgets-api',
        parent: '/Users/dev/projects',
        home: '/Users/dev/projects',
        entries: [],
      });
    } else {
      await json(route, [workSource()]);
    }
  });

  return { calls, bodies };
}

function mountPrTab(mount: Parameters<Parameters<typeof test>[1]>[0]['mount']) {
  return mount(
    <TestProviders initialEntries={['/work?view=prs']}>
      <WorkBacklogPage />
      <Toaster />
    </TestProviders>,
  );
}

test('the pull-request tab lists open PRs with their repository and branches', async ({
  mount,
  page,
}) => {
  await mockPrs(page, {
    prs: [
      pullRequest(),
      pullRequest({ id: 2, external_id: 502, title: 'Draft work', is_draft: true }),
    ],
  });
  await mountPrTab(mount);

  await expect(page.getByRole('tab', { name: 'Pull requests' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page.getByLabel('2 pull requests')).toBeVisible();

  const row = page.getByRole('listitem').filter({ hasText: PR_TITLE });
  await expect(row).toContainText('#501');
  await expect(row).toContainText('widgets-api');
  await expect(row).toContainText('feature/retry-fix → main');
  await expect(row).toContainText('Alex Doe');
  await expect(row.getByRole('link', { name: 'Open PR #501 in Azure DevOps' })).toBeVisible();

  await expect(page.getByRole('listitem').filter({ hasText: 'Draft work' })).toContainText('Draft');
});

test('threads are only fetched once a pull request is expanded, then grouped by file', async ({
  mount,
  page,
}) => {
  const routes = await mockPrs(page, {
    threads: [
      prThread({ id: 1, external_id: 1, file_path: '/app/main.py', right_file_line: 42 }),
      prThread({
        id: 2,
        external_id: 2,
        file_path: null,
        right_file_line: null,
        comments: [prComment({ id: 2, content: 'Overall this looks good.' })],
      }),
      prThread({
        id: 3,
        external_id: 3,
        is_resolved: true,
        status: 'fixed',
        file_path: '/app/utils.py',
        right_file_line: 7,
        comments: [prComment({ id: 3, content: OLD_REVIEW })],
      }),
    ],
  });
  await mountPrTab(mount);
  await expect(page.getByRole('listitem').filter({ hasText: PR_TITLE })).toBeVisible();

  // Nothing asked for threads while every row was collapsed.
  expect(routes.calls.filter((c) => c.endsWith('/threads'))).toHaveLength(0);

  await page.getByRole('button', { name: 'Expand #501' }).click();

  // PR-level thread first, then the file groups in path order.
  await expect(page.getByText('On the pull request')).toBeVisible();
  await expect(page.getByText('/app/main.py')).toBeVisible();
  await expect(page.getByText(REVIEW)).toBeVisible();
  await expect(page.getByText('Line 42')).toBeVisible();

  // Two open threads carry comments; the resolved one does not count.
  await expect(page.getByLabel('2 unresolved comments')).toBeVisible();

  // A resolved thread starts folded, so its comment is not on screen yet.
  await expect(page.getByText(OLD_REVIEW)).toHaveCount(0);
  await page.getByRole('button', { name: 'Expand thread 3' }).click();
  await expect(page.getByText(OLD_REVIEW)).toBeVisible();
});

test('Fix comments delegates the PR when its repository is already known', async ({
  mount,
  page,
}) => {
  const routes = await mockPrs(page);
  await mountPrTab(mount);

  await page.getByRole('button', { name: 'Fix comments' }).click();

  await expect(page.getByText('Fixing PR #501')).toBeVisible();
  await expect(
    page.getByText('A factory run was launched from the assembled prompt.'),
  ).toBeVisible();
  expect(routes.calls).toContain('POST /api/v1/work/prs/1/delegate');
  // A known repository never asks for a folder.
  await expect(page.getByText('Choose the checkout for this repository')).toHaveCount(0);
});

test('an unknown repository asks for a folder once, then retries the delegate itself', async ({
  mount,
  page,
}) => {
  const routes = await mockPrs(page, {
    delegates: [
      delegateResponse({
        resolved: false,
        local_path: null,
        reason: 'No folder under the projects root matches this remote.',
        launch_id: null,
        run_id: null,
        prompt: '',
      }),
      delegateResponse(),
    ],
  });
  await mountPrTab(mount);

  await page.getByRole('button', { name: 'Fix comments' }).click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('Choose the checkout for this repository');
  await expect(dialog).toContainText('No folder under the projects root matches this remote.');

  await dialog.getByRole('button', { name: 'Use this folder' }).click();

  // The folder is remembered, and the same delegate is retried without a second click.
  await expect(page.getByText('Fixing PR #501')).toBeVisible();
  expect(routes.calls.filter((c) => c === 'POST /api/v1/work/prs/1/delegate')).toHaveLength(2);
  const saved = routes.bodies.map((b) => JSON.parse(b || '{}')).find((b) => b.local_path);
  expect(saved).toMatchObject({
    remote_url: 'https://dev.azure.com/acme/widgets/_git/widgets-api',
    local_path: '/Users/dev/projects/widgets-api',
  });
});
