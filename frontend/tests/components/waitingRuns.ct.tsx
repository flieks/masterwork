import { test, expect, type Page, type Route } from '@playwright/experimental-ct-react';
import { RunCard } from '~/features/sessions/components/RunCard';
import { WaitingRuns } from '~/features/sessions/components/WaitingRuns';
import { waitedFor } from '~/features/sessions/runs';
import { TestProviders } from './harness/TestProviders';
import { chatRun } from './harness/runFixtures';

/**
 * A run blocked on a question is silent by construction, which is exactly what
 * the abandoned rule reads as "gave up". These cover the two places that has to
 * be visible: the card's own chip, and the banner above the grid.
 */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

function mockWaiting(page: Page, sessions: unknown[]) {
  return page.route('**/api/v1/coding-sessions**', (route: Route) => {
    if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 204, headers: CORS });
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: CORS,
      body: JSON.stringify(sessions),
    });
  });
}

function waitingRun(overrides = {}) {
  const run = chatRun({
    id: 'blocked-1',
    title: 'widen the DevOps work sync',
    status: 'waiting_input',
    awaiting_input_since: new Date(Date.now() - 6 * 60 * 60 * 1000).toISOString(),
    ...overrides,
  });
  return {
    ...run,
    phases: run.phases.map(({ seq, name, agent, status, started_at, duration_ms }) => ({
      seq,
      name,
      agent,
      status,
      started_at,
      duration_ms,
    })),
  };
}

test('a blocked run is named, not left to look abandoned', async ({ mount, page }) => {
  await mockWaiting(page, [waitingRun()]);
  await mount(
    <TestProviders>
      <WaitingRuns />
    </TestProviders>,
  );

  await expect(page.getByTestId('waiting-runs')).toBeVisible();
  await expect(page.getByText('A run is waiting on you')).toBeVisible();
  await expect(page.getByTestId('waiting-run')).toContainText('widen the DevOps work sync');
  // The number that makes the point: this one has been sitting all night.
  await expect(page.getByTestId('waiting-run')).toContainText('waiting 6h');
});

test('nothing is drawn when no run is blocked', async ({ mount, page }) => {
  await mockWaiting(page, []);
  await mount(
    <TestProviders>
      <WaitingRuns />
    </TestProviders>,
  );

  await expect(page.getByTestId('waiting-runs')).toHaveCount(0);
});

test('the count leads once more than one run is stuck', async ({ mount, page }) => {
  await mockWaiting(page, [waitingRun(), waitingRun({ id: 'blocked-2', title: 'second run' })]);
  await mount(
    <TestProviders>
      <WaitingRuns />
    </TestProviders>,
  );

  await expect(page.getByText('2 runs are waiting')).toBeVisible();
  await expect(page.getByTestId('waiting-run')).toHaveCount(2);
});

test('the card wears the waiting chip instead of running or abandoned', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <RunCard session={waitingRun()} now={Date.now()} />
    </TestProviders>,
  );

  // The chip reads "waiting", not the raw `waiting_input` the API sends.
  await expect(page.getByText('waiting', { exact: true })).toBeVisible();
  await expect(page.getByText('abandoned')).toHaveCount(0);
});

test('the wait is counted from when the question was asked', () => {
  const asked = '2026-08-16T03:11:31.000Z';
  expect(waitedFor(asked, Date.parse('2026-08-16T09:00:54.000Z'))).toBe('waiting 5h 49m');
  expect(waitedFor('not a date')).toBe('waiting');
});
