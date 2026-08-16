import { test, expect, type Page } from '@playwright/experimental-ct-react';
import type { FactoryRun } from '~/api/generated';
import { SessionRunBanner } from '~/features/sessions/components/SessionRunBanner';
import { TestProviders } from './harness/TestProviders';

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

const SESSION_ID = 'sess-build-1';

function factoryRun(overrides: Partial<FactoryRun> = {}): FactoryRun {
  return {
    run_id: 'aaaa1111',
    project_path: '/Users/dev/Projects/masterwork',
    project_name: 'masterwork',
    outcome: 'stopped',
    state: 'stopped',
    request_text: 'Add a context-growth series\n\nDETAILS…',
    branch: 'factory/aaaa1111',
    reason: 'cost cap reached: $32.95 of $25 budget',
    interview: false,
    accepted: false,
    started_at: '2026-08-15T09:40:02+00:00',
    ended_at: '2026-08-15T10:14:17+00:00',
    resumable: true,
    resume_hint: null,
    session_ids: [SESSION_ID],
    ...overrides,
  };
}

async function mockRun(
  page: Page,
  run: FactoryRun | null,
): Promise<{ resumes: string[]; launches: string[] }> {
  const resumes: string[] = [];
  const launches: string[] = [];
  await page.route('**/api/v1/launcher/**', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    let body: unknown = run;
    if (request.url().endsWith('/launch')) {
      launches.push(request.postData() ?? '');
      body = { id: 1, project_path: 'p', request_text: 'r', mode: 'autonomous', launched_at: '2026-08-16T00:00:00Z', pid: 1, run_id: null, launched: true };
    } else if (request.url().endsWith('/resume')) {
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
  return { resumes, launches };
}

function mountBanner(mount: Parameters<Parameters<typeof test>[1]>[0]['mount']) {
  return mount(
    <TestProviders>
      <SessionRunBanner sessionId={SESSION_ID} />
    </TestProviders>,
  );
}

test('a session from a stopped run can resume it from here', async ({ mount, page }) => {
  const { resumes } = await mockRun(page, factoryRun());
  await mountBanner(mount);

  await expect(page.getByText('Add a context-growth series')).toBeVisible();
  await expect(page.getByText(/Factory run aaaa1111/)).toBeVisible();
  await expect(page.getByText('stopped', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Resume run aaaa1111' }).click();
  await expect.poll(() => resumes.length).toBe(1);
  expect(JSON.parse(resumes[0])).toEqual({
    project_path: '/Users/dev/Projects/masterwork',
    run_id: 'aaaa1111',
  });
});

test('a session no run owns shows no banner at all', async ({ mount, page }) => {
  await mockRun(page, null);
  const component = await mountBanner(mount);
  await expect(component).toBeEmpty();
});

test('a completed run explains itself instead of offering Resume', async ({ mount, page }) => {
  await mockRun(
    page,
    factoryRun({
      outcome: 'done',
      state: 'finished',
      accepted: true,
      reason: 'all 5 stage(s) passed, checks green, review approved',
      resumable: false,
      resume_hint: 'completed and approved — nothing to resume',
    }),
  );
  await mountBanner(mount);

  await expect(page.getByText('done', { exact: true })).toBeVisible();
  await expect(page.getByText('completed and approved — nothing to resume')).toBeVisible();
  await expect(page.getByRole('button', { name: /^Resume run/ })).toHaveCount(0);
});

test('a run that moved on offers the rerun from the session page too', async ({ mount, page }) => {
  const { launches } = await mockRun(
    page,
    factoryRun({
      resumable: false,
      resume_hint: "'factory/aaaa1111' has moved on since this run left it",
    }),
  );
  await mountBanner(mount);

  await page.getByRole('button', { name: 'Run aaaa1111 again' }).click();
  await page.getByRole('button', { name: 'Start it' }).click();

  await expect.poll(() => launches.length).toBe(1);
  expect(JSON.parse(launches[0]).request_text).toContain('Add a context-growth series');
});
