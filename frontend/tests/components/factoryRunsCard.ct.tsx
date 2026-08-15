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
    outcome: 'stopped',
    state: 'stopped',
    request_text: 'Add a context-growth series to the observability\n\nDETAILS…',
    branch: 'factory/aaaa1111',
    reason: 'cost cap reached: $32.95 of $25 budget',
    interview: false,
    accepted: false,
    started_at: '2026-08-15T09:40:02+00:00',
    ended_at: '2026-08-15T10:14:17+00:00',
    resumable: true,
    resume_hint: null,
    session_ids: [],
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
      outcome: 'done',
      state: 'finished',
      accepted: true,
      reason: null,
      resumable: false,
      resume_hint: 'completed and approved — nothing to resume',
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
  await expect(page.getByText('done', { exact: true })).toHaveCount(0); // folded away
  await page.getByRole('button', { name: /Show 1 completed run/ }).click();
  await expect(page.getByText('done', { exact: true })).toBeVisible();
});

test('a run row links to the session the run reports itself as', async ({ mount, page }) => {
  await mockRuns(page, [factoryRun()]);
  await mountCard(mount);

  await expect(page.getByRole('link', { name: /Add a context-growth series/ })).toHaveAttribute(
    'href',
    '/sessions/factory-aaaa1111',
  );
});

test('a rejected run reads as failed, not finished, and offers Resume', async ({ mount, page }) => {
  // Both a rejected and an approved run end run.json's state as "finished";
  // the badge has to tell them apart or the list reads as all-green.
  await mockRuns(page, [
    factoryRun({
      run_id: 'cccc3333',
      outcome: 'failed',
      state: 'finished',
      reason: 'review did not approve within 2 round(s)',
      request_text: 'Make interview mode real',
    }),
  ]);
  await mountCard(mount);

  await expect(page.getByText('failed', { exact: true })).toBeVisible();
  await expect(page.getByText('finished')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Resume run cccc3333' })).toBeVisible();
});

test('a run that cannot be resumed says why, where its button would be', async ({ mount, page }) => {
  await mockRuns(page, [
    factoryRun({
      run_id: 'dddd4444',
      outcome: 'failed',
      state: 'finished',
      branch: null,
      resumable: false,
      resume_hint: 'no branch recorded — nothing safe to resume onto',
    }),
  ]);
  await mountCard(mount);

  await expect(page.getByText('no branch recorded — nothing safe to resume onto')).toBeVisible();
  await expect(page.getByRole('button', { name: /^Resume run/ })).toHaveCount(0);
});

test('with only completed runs the card folds them all away behind the toggle', async ({
  mount,
  page,
}) => {
  await mockRuns(page, [
    factoryRun({
      run_id: 'eeee5555',
      outcome: 'done',
      state: 'finished',
      accepted: true,
      resumable: false,
      resume_hint: 'completed and approved — nothing to resume',
    }),
  ]);
  await mountCard(mount);

  await expect(page.getByText(/Nothing needs a decision/)).toBeVisible();
  await expect(page.getByText('done', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Show 1 completed run' }).click();
  await expect(page.getByText('done', { exact: true })).toBeVisible();
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
