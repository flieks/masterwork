import { test, expect, type Page, type Route } from '@playwright/experimental-ct-react';
import { LaunchSessionDialog } from '~/features/sessions/components/LaunchSessionDialog';
import { Toaster } from '~/components/ui/sonner';
import { TestProviders } from './harness/TestProviders';

// The generated client targets a cross-origin backend (localhost:8008), so every
// fulfilled response needs CORS headers and OPTIONS preflights must be answered.
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

const PROJECTS_ROOT = '/Users/test/Projects';
const PROJECTS = [
  { name: 'alpha', path: `${PROJECTS_ROOT}/alpha`, is_git_repo: true },
  { name: 'beta', path: `${PROJECTS_ROOT}/beta`, is_git_repo: true },
];

function json(route: Route, status: number, body: unknown) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    headers: CORS,
    body: JSON.stringify(body),
  });
}

/** Records every launch/create POST body so tests can assert on it. */
async function mockLauncher(page: Page) {
  const launchCalls: unknown[] = [];
  const createCalls: unknown[] = [];
  // A GET after a create must list the new folder too, or selecting it (by
  // value) would point at an <option> that was never rendered.
  const projects = [...PROJECTS];

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const url = request.url();

    if (url.includes('/settings') && request.method() === 'GET') {
      await json(route, 200, { projects_root: PROJECTS_ROOT });
      return;
    }
    if (url.includes('/launcher/projects') && request.method() === 'GET') {
      await json(route, 200, projects);
      return;
    }
    if (url.includes('/launcher/projects') && request.method() === 'POST') {
      const body = JSON.parse(request.postData() ?? '{}');
      createCalls.push(body);
      const created = { name: body.name, path: `${PROJECTS_ROOT}/${body.name}`, is_git_repo: true };
      projects.push(created);
      await json(route, 201, created);
      return;
    }
    if (url.includes('/launcher/launch') && request.method() === 'POST') {
      const body = JSON.parse(request.postData() ?? '{}');
      launchCalls.push(body);
      await json(route, 200, {
        id: 1,
        project_path: body.project_path,
        request_text: body.request_text,
        mode: body.mode ?? 'autonomous',
        launched_at: new Date(0).toISOString(),
        pid: 4242,
        launched: true,
      });
      return;
    }

    throw new Error(`unexpected request in launchSessionDialog test: ${request.method()} ${url}`);
  });

  return { launchCalls, createCalls };
}

test('the request is required before Launch is enabled, project options come from the API', async ({
  mount,
  page,
}) => {
  await mockLauncher(page);

  await mount(
    <TestProviders>
      <LaunchSessionDialog open onOpenChange={() => {}} />
    </TestProviders>,
  );

  const launch = page.getByRole('button', { name: 'Launch' });
  await expect(launch).toBeDisabled();

  const select = page.getByLabel('Project');
  await expect(select.getByRole('option')).toHaveCount(3); // placeholder + alpha + beta
  await select.selectOption(PROJECTS[0].path);
  await expect(launch).toBeDisabled(); // still no request text

  await page.getByLabel('Request').fill('Add a subtract function');
  await expect(launch).toBeEnabled();
});

test('the mode radio defaults to autonomous; picking interview changes what gets posted', async ({
  mount,
  page,
}) => {
  const { launchCalls } = await mockLauncher(page);

  await mount(
    <TestProviders>
      <LaunchSessionDialog open onOpenChange={() => {}} />
    </TestProviders>,
  );

  const autonomous = page.getByRole('radio', {
    name: 'Fully autonomous: plan and build with best-guess assumptions, never ask me',
  });
  const interview = page.getByRole('radio', {
    name: 'Interview me: pause on weak assumptions before building',
  });
  await expect(autonomous).toBeChecked();
  await expect(interview).not.toBeChecked();

  await page.getByLabel('Project').selectOption(PROJECTS[0].path);
  await page.getByLabel('Request').fill('Add a subtract function');
  await interview.check();

  await page.getByRole('button', { name: 'Launch' }).click();
  await expect.poll(() => launchCalls.length).toBe(1);
  expect(launchCalls[0]).toMatchObject({ mode: 'interview' });
});

test('the new-folder affordance creates a project and selects it', async ({ mount, page }) => {
  const { createCalls } = await mockLauncher(page);

  await mount(
    <TestProviders>
      <LaunchSessionDialog open onOpenChange={() => {}} />
    </TestProviders>,
  );

  await page.getByLabel('New folder name').fill('gamma');
  await page.getByRole('button', { name: 'Create' }).click();

  await expect.poll(() => createCalls.length).toBe(1);
  expect(createCalls[0]).toMatchObject({ name: 'gamma' });
  await expect(page.getByLabel('Project')).toHaveValue(`${PROJECTS_ROOT}/gamma`);
});

test('Launch posts project_path and request_text, then reports the run started', async ({
  mount,
  page,
}) => {
  const { launchCalls } = await mockLauncher(page);
  let closed = false;

  await mount(
    <TestProviders>
      <LaunchSessionDialog open onOpenChange={(next) => (closed = !next)} />
      <Toaster />
    </TestProviders>,
  );

  await page.getByLabel('Project').selectOption(PROJECTS[1].path);
  await page.getByLabel('Request').fill('Wire up the billing webhook');
  await page.getByRole('button', { name: 'Launch' }).click();

  await expect.poll(() => launchCalls.length).toBe(1);
  expect(launchCalls[0]).toMatchObject({
    project_path: PROJECTS[1].path,
    request_text: 'Wire up the billing webhook',
  });
  await expect.poll(() => closed).toBe(true);
  await expect(page.getByText('Factory run started')).toBeVisible();
});
