import { test, expect, type Page } from '@playwright/experimental-ct-react';
import type { FactoryRun } from '~/api/generated';
import { FactoryRunsCard } from '~/features/sessions/components/FactoryRunsCard';
import { TestProviders } from './harness/TestProviders';

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

function factoryRun(overrides: Partial<FactoryRun> = {}): FactoryRun {
  return {
    run_id: 'aaaa1111',
    project_path: '/Users/dev/Projects/masterwork',
    project_name: 'masterwork',
    state: 'stopped',
    request_text: 'Add a context-growth series to the observability\n\nDETAILS…',
    branch: 'factory/aaaa1111',
    reason: 'cost cap reached: $32.95 of $25 budget',
    interview: false,
    accepted: false,
    started_at: '2026-08-15T09:40:02+00:00',
    ended_at: '2026-08-15T10:14:17+00:00',
    resumable: true,
    ...overrides,
  };
}

async function mockRuns(page: Page, runs: FactoryRun[]): Promise<{ resumes: string[] }> {
  const resumes: string[] = [];
  await page.route('**/api/v1/launcher/runs**', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    let body: unknown = runs;
    if (request.url().endsWith('/resume')) {
      resumes.push(request.postData() ?? '');
      body = { run_id: 'aaaa1111', resumed: true, pid: 4242 };
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: CORS,
      body: JSON.stringify(body),
    });
  });
  return { resumes };
}

function mountCard(mount: Parameters<Parameters<typeof test>[1]>[0]['mount']) {
  return mount(
    <TestProviders>
      <FactoryRunsCard />
    </TestProviders>,
  );
}

test('a stopped run shows its state, reason and a Resume button', async ({ mount, page }) => {
  await mockRuns(page, [
    factoryRun(),
    factoryRun({
      run_id: 'bbbb2222',
      state: 'finished',
      accepted: true,
      reason: null,
      resumable: false,
      request_text: 'Build the folder picker',
    }),
  ]);
  await mountCard(mount);

  await expect(page.getByText('Factory runs')).toBeVisible();
  await expect(page.getByText('stopped', { exact: true })).toBeVisible();
  await expect(page.getByText(/cost cap reached/)).toBeVisible();
  await expect(page.getByText('Add a context-growth series to the observability')).toBeVisible();

  // Only the resumable row offers the button; the accepted run is done.
  await expect(page.getByRole('button', { name: 'Resume run aaaa1111' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Resume run bbbb2222' })).toHaveCount(0);
  await expect(page.getByText('finished')).toBeVisible();
});

test('Resume posts the run and reports success', async ({ mount, page }) => {
  const { resumes } = await mockRuns(page, [factoryRun()]);
  await mountCard(mount);

  await page.getByRole('button', { name: 'Resume run aaaa1111' }).click();

  await expect.poll(() => resumes.length).toBe(1);
  expect(JSON.parse(resumes[0])).toEqual({
    project_path: '/Users/dev/Projects/masterwork',
    run_id: 'aaaa1111',
  });
});

test('with no runs recorded the card stays off the page', async ({ mount, page }) => {
  await mockRuns(page, []);
  const component = await mountCard(mount);
  await expect(component).toBeEmpty();
});
