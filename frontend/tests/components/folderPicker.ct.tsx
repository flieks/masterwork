import { test, expect, type Page, type Route } from '@playwright/experimental-ct-react';
import { LaunchSessionDialog } from '~/features/sessions/components/LaunchSessionDialog';
import { TestProviders } from './harness/TestProviders';

// The generated client targets a cross-origin backend (localhost:8008), so every
// fulfilled response needs CORS headers and OPTIONS preflights must be answered.
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

function json(route: Route, status: number, body: unknown) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    headers: CORS,
    body: JSON.stringify(body),
  });
}

interface TreeNode {
  parent: string | null;
  entries: { name: string; path: string }[];
}

/** An in-memory `browse` tree plus the settings/projects endpoints the dialog
 * also queries on mount. Each test uses its own root — the shared TestProviders
 * QueryClient would otherwise serve one test's cached listing to another. */
async function mockFolderPicker(
  page: Page,
  root: string,
  tree: Record<string, TreeNode>,
  opts: { failOnce?: string; home?: string } = {},
) {
  const patchCalls: unknown[] = [];
  const browseCalls: string[] = [];
  let failedOnce = false;

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const url = new URL(request.url());

    if (url.pathname.endsWith('/settings') && request.method() === 'GET') {
      await json(route, 200, { projects_root: root });
      return;
    }
    if (url.pathname.endsWith('/settings') && request.method() === 'PATCH') {
      const body = JSON.parse(request.postData() ?? '{}');
      patchCalls.push(body);
      await json(route, 200, { projects_root: body.projects_root });
      return;
    }
    if (url.pathname.endsWith('/launcher/projects') && request.method() === 'GET') {
      await json(route, 200, []);
      return;
    }
    if (url.pathname.endsWith('/launcher/browse') && request.method() === 'GET') {
      const path = url.searchParams.get('path') ?? root;
      browseCalls.push(path);
      if (opts.failOnce === path && !failedOnce) {
        failedOnce = true;
        await route.fulfill({ status: 500, headers: CORS, body: '' });
        return;
      }
      const node = tree[path];
      if (!node) throw new Error(`unexpected browse path in test: ${path}`);
      await json(route, 200, {
        path,
        parent: node.parent,
        entries: node.entries,
        home: opts.home ?? '/Users/nobody',
      });
      return;
    }

    throw new Error(`unexpected request in folderPicker test: ${request.method()} ${url}`);
  });

  return { patchCalls, browseCalls };
}

test('navigating the picker updates the breadcrumb and rows; Use this folder saves the path', async ({
  mount,
  page,
}) => {
  const root = '/Users/nav-test/Projects';
  const tree: Record<string, TreeNode> = {
    [root]: {
      parent: '/Users/nav-test',
      entries: [
        { name: 'alpha', path: `${root}/alpha` },
        { name: 'beta', path: `${root}/beta` },
      ],
    },
    [`${root}/alpha`]: {
      parent: root,
      entries: [{ name: 'nested', path: `${root}/alpha/nested` }],
    },
  };
  const { patchCalls, browseCalls } = await mockFolderPicker(page, root, tree);

  await mount(
    <TestProviders>
      <LaunchSessionDialog open onOpenChange={() => {}} />
    </TestProviders>,
  );

  await page.getByRole('button', { name: 'Browse' }).click();

  await expect(page.getByRole('button', { name: 'Projects', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'alpha', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'beta', exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'alpha', exact: true }).click();

  await expect.poll(() => browseCalls).toContain(`${root}/alpha`);
  await expect(page.getByRole('button', { name: 'nested', exact: true })).toBeVisible();
  // the breadcrumb now ends in the descended-into folder
  await expect(page.getByRole('button', { name: 'alpha', exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Use this folder' }).click();

  await expect.poll(() => patchCalls.length).toBe(1);
  expect(patchCalls[0]).toMatchObject({ projects_root: `${root}/alpha` });
  await expect(page.getByRole('button', { name: 'Use this folder' })).toHaveCount(0);
  await expect(page.getByLabel('Projects root')).toHaveValue(`${root}/alpha`);
});

test('Up one level returns to the parent, and is disabled at the filesystem root', async ({
  mount,
  page,
}) => {
  const root = '/Users/up-test/Projects';
  const parentDir = '/Users/up-test';
  const tree: Record<string, TreeNode> = {
    [root]: { parent: parentDir, entries: [{ name: 'alpha', path: `${root}/alpha` }] },
    [parentDir]: { parent: '/', entries: [{ name: 'Projects', path: root }] },
    '/': { parent: null, entries: [{ name: 'Users', path: '/Users' }] },
  };
  const { browseCalls } = await mockFolderPicker(page, root, tree);

  await mount(
    <TestProviders>
      <LaunchSessionDialog open onOpenChange={() => {}} />
    </TestProviders>,
  );

  await page.getByRole('button', { name: 'Browse' }).click();

  const upOneLevel = page.getByRole('button', { name: 'Up one level' });
  await expect(upOneLevel).toBeEnabled();

  await upOneLevel.click();
  await expect.poll(() => browseCalls).toContain(parentDir);
  await expect(page.getByRole('button', { name: 'Projects', exact: true })).toBeVisible();
  await expect(upOneLevel).toBeEnabled();

  await upOneLevel.click();
  await expect.poll(() => browseCalls).toContain('/');
  await expect(upOneLevel).toBeDisabled();
});

test('a failed browse renders the inline error, and Retry re-issues the request', async ({
  mount,
  page,
}) => {
  const root = '/Users/err-test/Projects';
  const tree: Record<string, TreeNode> = {
    [root]: { parent: '/Users/err-test', entries: [] },
  };
  const { browseCalls } = await mockFolderPicker(page, root, tree, { failOnce: root });

  await mount(
    <TestProviders>
      <LaunchSessionDialog open onOpenChange={() => {}} />
    </TestProviders>,
  );

  await page.getByRole('button', { name: 'Browse' }).click();
  await expect(page.getByText("Couldn't browse that folder.")).toBeVisible();

  await page.getByRole('button', { name: 'Retry' }).click();
  await expect.poll(() => browseCalls.length).toBe(2);
  await expect(page.getByText('No subfolders here.')).toBeVisible();
});

test('the sidebar jumps to the home directory the server reported, and Cancel saves nothing', async ({
  mount,
  page,
}) => {
  const home = '/Users/side-test';
  const root = `${home}/Projects`;
  const tree: Record<string, TreeNode> = {
    [root]: { parent: home, entries: [{ name: 'alpha', path: `${root}/alpha` }] },
    [home]: { parent: '/Users', entries: [{ name: 'Projects', path: root }] },
  };
  const { patchCalls, browseCalls } = await mockFolderPicker(page, root, tree, { home });

  await mount(
    <TestProviders>
      <LaunchSessionDialog open onOpenChange={() => {}} />
    </TestProviders>,
  );

  await page.getByRole('button', { name: 'Browse' }).click();
  await expect(page.getByRole('button', { name: 'alpha', exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Home', exact: true }).click();
  await expect.poll(() => browseCalls).toContain(home);
  await expect(page.getByRole('button', { name: 'Projects', exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Cancel' }).click();
  await expect(page.getByRole('button', { name: 'Use this folder' })).toHaveCount(0);
  expect(patchCalls).toEqual([]);
  await expect(page.getByLabel('Projects root')).toHaveValue(root);
});
