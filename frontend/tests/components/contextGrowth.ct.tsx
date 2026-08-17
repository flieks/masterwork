import { test, expect, type Page } from '@playwright/experimental-ct-react';
import { Route, Routes } from 'react-router-dom';
import type { CodingSessionDetail, ContextSeries } from '~/api/generated';
import { SessionDetailPage } from '~/features/sessions/components/SessionDetailPage';
import { TestProviders } from './harness/TestProviders';
import { contextSample, contextSeries, factoryRun } from './harness/runFixtures';

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

const RUN = factoryRun();

const SERIES: ContextSeries = contextSeries({
  session_id: RUN.id,
  baseline_tokens: 20_000,
  peak_tokens: 60_000,
  samples: [
    contextSample(1, 20_000),
    contextSample(2, 45_000, { delta_tokens: 25_000, tools: ['Read'] }),
    contextSample(3, 30_000, { delta_tokens: -15_000, is_truncation: true }),
    contextSample(4, 60_000, { delta_tokens: 30_000, tools: ['Edit'] }),
  ],
  tools: [
    { tool: 'Edit', delta_tokens: 30_000, calls: 1 },
    { tool: 'Read', delta_tokens: 25_000, calls: 1 },
  ],
});

async function mockRun(
  page: Page,
  run: CodingSessionDetail = RUN,
  series: ContextSeries = SERIES,
): Promise<void> {
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const pathname = new URL(request.url()).pathname;
    if (pathname.includes('/launcher/')) {
      // No factory run owns this session, so its banner stays off the page.
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: CORS,
        body: 'null',
      });
      return;
    }
    const body = pathname.endsWith('/events')
      ? []
      : pathname.endsWith('/context')
        ? series
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

test('draws a point per sample and marks the truncation', async ({ mount, page }) => {
  await mockRun(page);
  await mountDetail(mount);

  const panel = page.getByLabel('Context growth');
  await expect(panel).toBeVisible();

  const chart = panel.getByLabel('Total context tokens over the run');
  await expect(chart.locator('[data-context-point]')).toHaveCount(4);
  await expect(chart.locator('[data-truncation="true"]')).toHaveCount(1);
  await expect(panel.getByText(/1 context truncation/)).toBeVisible();
});

test('the tool list is ranked highest-delta first', async ({ mount, page }) => {
  await mockRun(page);
  await mountDetail(mount);

  const rows = page.getByLabel('Context growth').getByRole('button');
  await expect(rows.first()).toContainText('Edit');
  await expect(rows.nth(1)).toContainText('Read');
});

test('clicking a tool row switches to All events and filters the stream', async ({
  mount,
  page,
}) => {
  await mockRun(page);
  await mountDetail(mount);

  await page.getByRole('button', { name: /Edit/ }).click();

  await expect(page.getByRole('tab', { name: /All events/, selected: true })).toBeVisible();
  await expect(page.getByText('Filtered to')).toBeVisible();
});

test('an empty series renders no panel at all', async ({ mount, page }) => {
  await mockRun(page, RUN, contextSeries({ session_id: RUN.id }));
  await mountDetail(mount);

  await expect(page.getByLabel('Context growth')).toHaveCount(0);
});
