import { test, expect, type Page } from '@playwright/experimental-ct-react';
import { Route, Routes } from 'react-router-dom';
import type { CodingEvent } from '~/api/generated';
import { SessionDetailPage } from '~/features/sessions/components/SessionDetailPage';
import { TestProviders } from './harness/TestProviders';
import { denseChatRun, factoryRun, toolCall } from './harness/runFixtures';

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

const RUN = factoryRun();
const PLAN_ID = RUN.phases[0].id;
const REVIEW_ID = RUN.phases[3].id;

const EVENTS: CodingEvent[] = [
  toolCall(1, PLAN_ID, '2026-08-08T00:00:22.000Z'),
  { ...toolCall(2, PLAN_ID, '2026-08-08T00:00:30.000Z'), tool_name: 'Glob' },
  {
    ...toolCall(3, REVIEW_ID, '2026-08-08T00:01:20.000Z'),
    event_type: 'gate_fail',
    tool_name: null,
    payload: { detail: 'tests must cover the zero case' },
  },
];

async function mockRun(page: Page, run: typeof RUN = RUN): Promise<void> {
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const url = new URL(request.url());
    const body = url.pathname.endsWith('/events')
      ? EVENTS.filter((e) => e.id > Number(url.searchParams.get('after') ?? 0))
      : run;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: CORS,
      body: JSON.stringify(body),
    });
  });
}

function mountDetail(mount: Parameters<Parameters<typeof test>[1]>[0]['mount']) {
  return mount(
    <TestProviders initialEntries={['/sessions/factory-3f5a20b0']}>
      <Routes>
        <Route path="/sessions/:id" element={<SessionDetailPage />} />
      </Routes>
    </TestProviders>,
  );
}

test('the run header states the request, the outcome and the telemetry', async ({
  mount,
  page,
}) => {
  await mockRun(page);
  await mountDetail(mount);

  await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toContainText('Sessions');
  await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toContainText(
    'factory-3f5a20b0',
  );
  await expect(
    page.getByRole('heading', { name: /Add a subtract\(a, b\) function/ }),
  ).toBeVisible();
  await expect(page.getByText('success')).toBeVisible();
  await expect(page.getByText(/started Aug 8, 2026/)).toBeVisible();

  // Cost, duration, total / cache-read / output tokens. The cost carries its
  // own `$` and no longer doubles one up with a dollar icon.
  await expect(page.getByTitle('Cost')).toHaveText('Cost: $0.1924');
  await expect(page.getByText('1m 54s active')).toBeVisible();
  await expect(page.getByText('899.9k')).toBeVisible();
  await expect(page.getByText('1.11M')).toBeVisible();
  await expect(page.getByText('8.3k')).toBeVisible();
});

test('clicking a phase opens its panel with gates, commit and its own events', async ({
  mount,
  page,
}) => {
  await mockRun(page);
  await mountDetail(mount);

  await expect(page.getByText('Select a phase to see its events')).toBeVisible();

  await page.getByRole('button', { name: 'Phase review' }).click();

  const panel = page.getByLabel('Phase detail');
  await expect(panel.getByRole('heading', { name: /review/ })).toBeVisible();
  await expect(panel.getByText('9 gates passed')).toBeVisible();
  await expect(panel.getByText('1 failed')).toBeVisible();
  await expect(panel.getByText('8fc376e')).toBeVisible();
  // The correction this stage cost is surfaced, not buried in the payload.
  await expect(panel.getByText('Corrections')).toBeVisible();
  await expect(panel.getByText('1', { exact: true })).toBeVisible();

  // Only this phase's events — the plan tool calls stay out.
  await expect(panel.getByText('gate_fail')).toBeVisible();
  await expect(panel.getByText('Glob')).toHaveCount(0);

  // Clicking the same phase again closes the panel.
  await page.getByRole('button', { name: 'Phase review' }).click();
  await expect(page.getByText('Select a phase to see its events')).toBeVisible();
});

test('a phase with no events of its own says so', async ({ mount, page }) => {
  await mockRun(page);
  await mountDetail(mount);

  await page.getByRole('button', { name: 'Phase document' }).click();
  await expect(page.getByText('This phase recorded no events of its own.')).toBeVisible();
});

test('a marker phase explains why it holds nothing, rather than reading as 0ms', async ({
  mount,
  page,
}) => {
  await mockRun(page, denseChatRun());
  await mountDetail(mount);

  await page
    .locator('[data-lane="subagent"]')
    .getByRole('button', { name: 'Phase turn 1' })
    .click();

  const panel = page.getByLabel('Phase detail');
  // The length nobody measured is not reported as a measurement of zero.
  await expect(panel.getByText('not recorded', { exact: true })).toBeVisible();
  await expect(panel.getByText('0ms')).toHaveCount(0);
  await expect(panel.getByText(/attributed to the main lane/)).toBeVisible();
});

test('the full event stream stays one tab away', async ({ mount, page }) => {
  await mockRun(page);
  await mountDetail(mount);

  await page.getByRole('tab', { name: /All events/ }).click();

  // Every event, regardless of phase, with the payload disclosure intact.
  await expect(page.getByText('gate_fail')).toBeVisible();
  await expect(page.getByText('Glob')).toBeVisible();
  await page
    .getByRole('button', { name: /^tool_call/ })
    .first()
    .click();
  await expect(page.getByText(/"file_path": "calc.py"/)).toBeVisible();
});
