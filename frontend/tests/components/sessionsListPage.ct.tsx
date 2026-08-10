import { test, expect, type Page } from '@playwright/experimental-ct-react';
import type { CodingSession } from '~/api/generated';
import { SessionsListPage } from '~/features/sessions/components/SessionsListPage';
import { TestProviders } from './harness/TestProviders';
import { chatRun, factoryRunSummary } from './harness/runFixtures';
import { disconnected, integration } from './harness/integrationFixtures';

// The generated client targets a cross-origin backend (localhost:8008), so every
// fulfilled response needs CORS headers and OPTIONS preflights must be answered.
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

/** A chat session as the list endpoint returns it: PhaseSummary rows. */
function chatSummary(overrides: Partial<CodingSession> = {}): CodingSession {
  const run = chatRun();
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
    ...overrides,
  };
}

async function mockSessions(page: Page, sessions: CodingSession[]): Promise<{ urls: string[] }> {
  const urls: string[] = [];
  await page.route('**/api/v1/**', async (route) => {
    if (route.request().method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const url = route.request().url();
    // The screen also asks whether anything is recording; these tests are about
    // the runs, so the agent is always connected, the banner stays quiet, and
    // its request is kept out of the recorded list the filters are asserted on.
    const setup = url.includes('/observability/');
    if (!setup) urls.push(url);
    const body = setup ? [integration()] : sessions;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: CORS,
      body: JSON.stringify(body),
    });
  });
  return { urls };
}

test('runs are cards in one grid, pipeline runs and chat sessions alike', async ({
  mount,
  page,
}) => {
  await mockSessions(page, [factoryRunSummary(), chatSummary()]);

  await mount(
    <TestProviders>
      <SessionsListPage />
    </TestProviders>,
  );

  await expect(page.getByRole('heading', { name: 'Sessions' })).toBeVisible();
  await expect(page.getByText('2 runs')).toBeVisible();

  const cards = page.getByRole('link');
  await expect(cards).toHaveCount(2);

  const factory = cards.first();
  await expect(factory).toContainText('factory-3f5a20b0');
  await expect(factory).toContainText('factory');
  await expect(factory).toContainText('Add a subtract(a, b) function');
  await expect(factory).toContainText('$0.1924');
  await expect(factory).toContainText('899.9k');
  await expect(factory).toContainText('success');

  // A chat session gets the same card, not a separate section.
  const chat = cards.nth(1);
  await expect(chat).toContainText('d70244ff');
  await expect(chat).toContainText('chat');
  await expect(chat).toContainText('redesign the sessions screen');
  await expect(chat).toHaveAttribute('href', '/sessions/d70244ff-e3b3-4ee0-a615-12754b772de9');
});

test('marks an open session with recent activity as live', async ({ mount, page }) => {
  await mockSessions(page, [
    chatSummary({ ended_at: null, last_event_at: new Date().toISOString() }),
    // Open but silent for hours — stale, not live.
    chatSummary({ id: 'quizzy-session-id-0001', ended_at: null }),
  ]);

  await mount(
    <TestProviders>
      <SessionsListPage />
    </TestProviders>,
  );

  await expect(page.getByText('1 live')).toBeVisible();
  await expect(page.getByText('Live', { exact: true })).toHaveCount(1);
});

test('the workflow and status filters go into the request', async ({ mount, page }) => {
  const { urls } = await mockSessions(page, [factoryRunSummary()]);

  await mount(
    <TestProviders>
      <SessionsListPage />
    </TestProviders>,
  );

  await expect(page.getByText('1 run')).toBeVisible();
  expect(urls[0]).not.toContain('workflow=');

  await page
    .getByRole('group', { name: 'Workflow' })
    .getByRole('button', { name: 'Factory' })
    .click();
  await expect.poll(() => urls.some((u) => u.includes('workflow=factory'))).toBe(true);

  await page.getByRole('group', { name: 'Status' }).getByRole('button', { name: 'Failed' }).click();
  await expect
    .poll(() => urls.some((u) => u.includes('workflow=factory') && u.includes('status=failed')))
    .toBe(true);

  // "All" clears the filter rather than sending an empty value.
  await page.getByRole('group', { name: 'Status' }).getByRole('button', { name: 'All' }).click();
  await expect.poll(() => urls.at(-1)?.includes('status=') === false).toBe(true);
});

test('automated runs stay out of the grid until the toggle asks for them', async ({
  mount,
  page,
}) => {
  const scripted = factoryRunSummary({ id: 'factory-automated', launch_mode: 'automated' });
  await page.route('**/api/v1/**', async (route) => {
    if (route.request().method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const url = route.request().url();
    if (url.includes('/observability/')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: CORS,
        body: JSON.stringify([integration()]),
      });
      return;
    }
    // The backend does the filtering, so the toggle has to reach the request.
    const asked = url.includes('include_automated=true');
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: CORS,
      body: JSON.stringify(asked ? [factoryRunSummary(), scripted] : [factoryRunSummary()]),
    });
  });

  await mount(
    <TestProviders>
      <SessionsListPage />
    </TestProviders>,
  );

  await expect(page.getByRole('link')).toHaveCount(1);

  await page.getByRole('button', { name: 'Show automated' }).click();

  await expect(page.getByRole('link')).toHaveCount(2);
  await expect(page.getByText('factory-automated')).toBeVisible();
  await expect(page.getByLabel('Automated run')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Hide automated' })).toBeVisible();
});

test('a connected but empty screen says to go and code, not to go and configure', async ({
  mount,
  page,
}) => {
  await mockSessions(page, []);

  await mount(
    <TestProviders>
      <SessionsListPage />
    </TestProviders>,
  );

  await expect(page.getByText('No runs recorded yet')).toBeVisible();
  await expect(page.getByText(/Start a coding session/)).toBeVisible();
});

test('the interrupted filter admits nothing can match it, rather than blaming the runs', async ({
  mount,
  page,
}) => {
  // Nothing writes `interrupted`: masterwork cannot tell a killed process from
  // a lost hook, so it never derives one. The option stays for a producer that
  // learns to report it — the empty state has to say which of the two it is.
  await page.route('**/api/v1/**', async (route) => {
    if (route.request().method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const url = route.request().url();
    const setup = url.includes('/observability/');
    const asked = url.includes('status=interrupted');
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: CORS,
      body: JSON.stringify(setup ? [integration()] : asked ? [] : [factoryRunSummary()]),
    });
  });

  await mount(
    <TestProviders>
      <SessionsListPage />
    </TestProviders>,
  );
  await expect(page.getByRole('link')).toHaveCount(1);

  await page
    .getByRole('group', { name: 'Status' })
    .getByRole('button', { name: 'Interrupted' })
    .click();

  await expect(page.getByText('No run reports itself interrupted')).toBeVisible();
  await expect(page.getByText(/Masterwork never derives this status/)).toBeVisible();
  // Not the generic "go and code" copy, which would read as "none matched".
  await expect(page.getByText(/Start a coding session/)).toHaveCount(0);
});

test('an empty screen with nothing recording points at the connect card', async ({
  mount,
  page,
}) => {
  await page.route('**/api/v1/**', async (route) => {
    if (route.request().method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const setup = route.request().url().includes('/observability/');
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: CORS,
      body: JSON.stringify(setup ? [disconnected()] : []),
    });
  });

  await mount(
    <TestProviders>
      <SessionsListPage />
    </TestProviders>,
  );

  await expect(page.getByRole('button', { name: 'Connect Claude Code' })).toBeVisible();
  await expect(page.getByText(/Connect your coding agent above/)).toBeVisible();
});
