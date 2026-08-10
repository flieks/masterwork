import { test, expect, type Page } from '@playwright/experimental-ct-react';
import { AnalyticsPanel } from '~/features/analytics/components/AnalyticsPanel';
import { TestProviders } from './harness/TestProviders';
import { GATE_STATS, MODEL_STATS, ROLE_STATS, RUN_STATS } from './harness/analyticsFixtures';

/**
 * The cross-run analytics, and the four rules that keep them honest: a rate
 * never appears without the count it was divided by, a rate the API could not
 * compute is unknown rather than zero, the row that names no model says it is
 * not one, and a gate's note is shown whole.
 */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

/** Route each aggregate by its own path — the panel asks for all four at once. */
async function mockAnalytics(page: Page): Promise<void> {
  await page.route('**/api/v1/**', async (route) => {
    if (route.request().method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const { pathname } = new URL(route.request().url());
    const body = pathname.endsWith('/gates')
      ? GATE_STATS
      : pathname.endsWith('/roles')
        ? ROLE_STATS
        : pathname.endsWith('/runs')
          ? RUN_STATS
          : pathname.endsWith('/models')
            ? MODEL_STATS
            : [];
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: CORS,
      body: JSON.stringify(body),
    });
  });
}

function mountPanel(mount: Parameters<Parameters<typeof test>[1]>[0]['mount']) {
  return mount(
    <TestProviders>
      <AnalyticsPanel />
    </TestProviders>,
  );
}

test('a rate the API could not compute reads as unknown, never as zero', async ({
  mount,
  page,
}) => {
  await mockAnalytics(page);
  const panel = await mountPanel(mount);

  // `git` ran no gate and no envelope, so both its rates come back null. A `0%`
  // here would claim the lane never fails its gates, about gates it never ran.
  const gitRow = panel.locator('tr', { has: page.getByText('git', { exact: true }) });
  await expect(gitRow).toBeVisible();
  await expect(gitRow.getByText('—').first()).toBeVisible();
  await expect(gitRow.getByText('0 checks')).toBeVisible();
  await expect(gitRow.getByText('0 attempts')).toBeVisible();
  await expect(gitRow.getByText('0%')).toHaveCount(0);

  // The same rule on the chart: a run that reported no cost gets the dashed
  // "never said" tick rather than a bar sitting flat on the baseline.
  await panel.getByRole('group', { name: 'Plot' }).getByRole('button', { name: 'Cost' }).click();
  await expect(panel.locator('[data-run-bar="unknown"]')).toHaveCount(1);
  await expect(panel.getByText('1 run reported no cost')).toBeVisible();
});

test('every rate is shown with the count it was computed from', async ({ mount, page }) => {
  await mockAnalytics(page);
  const panel = await mountPanel(mount);

  // The gate header: 3 failures of 17 checks, and the denominator says 17.
  const envelope = panel.getByRole('group', { name: 'envelope' });
  await expect(envelope.getByText('18%').first()).toBeVisible();
  await expect(envelope.getByText('17 checks').first()).toBeVisible();

  // The role split under it: half of the plan role's six checks failed. Both
  // the 50 % and the 6 have to be on screen — 50 % of 6 and of 600 are not the
  // same claim, and only the denominator tells them apart.
  const planGateRow = envelope.locator('tr', { has: page.getByText('plan', { exact: true }) });
  await expect(planGateRow.getByText('50%')).toBeVisible();
  await expect(planGateRow.getByText('6 checks')).toBeVisible();

  // And in the role table, where the same rule covers averages as well as rates.
  const planRoleRow = panel
    .locator('tr', { has: page.getByText('plan', { exact: true }) })
    .filter({ hasText: '6 attempts' });
  await expect(planRoleRow.getByText('50%')).toBeVisible();
  await expect(planRoleRow.getByText('13 stages').first()).toBeVisible();

  const reviewRow = panel.locator('tr', { has: page.getByText('review', { exact: true }) }).last();
  await expect(reviewRow.getByText('0.58')).toBeVisible();
  await expect(reviewRow.getByText('12 stages').first()).toBeVisible();
});

test('the row that names no model is labelled, not left to read as one', async ({
  mount,
  page,
}) => {
  await mockAnalytics(page);
  const panel = await mountPanel(mount);

  const row = panel.locator('tr', { has: page.getByText('unattributed', { exact: true }) });
  await expect(row).toBeVisible();
  await expect(row.getByText('not a model')).toBeVisible();
  // Its acceptance rate is near the real model's by construction, so the table
  // has to say why rather than let the two be compared.
  await expect(row.getByText('83 runs')).toBeVisible();
  await expect(
    panel.getByText('double-counts runs by construction', { exact: false }),
  ).toBeVisible();
});

test("a gate's failure note is rendered in full, never truncated", async ({ mount, page }) => {
  await mockAnalytics(page);
  const panel = await mountPanel(mount);

  // The note names the three fields that were missing; a clipped one that
  // stopped at "missing required field(s)" would name none of them.
  const note = panel.getByText(
    'missing required field(s) for the plan role: status, artifacts, changed_files. End your reply with exactly one fenced ```json envelope block, nothing after it.',
    { exact: true },
  );
  await expect(note).toBeVisible();

  const rendered = await note.evaluate((el) => ({
    text: el.textContent ?? '',
    clipped: el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1,
  }));
  expect(rendered.text).toContain('status, artifacts, changed_files');
  expect(rendered.clipped).toBe(false);

  // Both distinct failures are listed rather than collapsed into one bucket,
  // and the one seen twice says so.
  await expect(panel.getByText('"status" must be one of ok, blocked, failed')).toBeVisible();
  await expect(panel.getByText('2 times')).toBeVisible();
});

test('a gate that never failed keeps its checks visible and folds only its split', async ({
  mount,
  page,
}) => {
  await mockAnalytics(page);
  const panel = await mountPanel(mount);

  const boundary = panel.getByRole('group', { name: 'boundary' });
  // The evidence that it ran stays on the header, unfolded.
  await expect(boundary.getByText('17 checks').first()).toBeVisible();
  await expect(boundary.getByText('0 failures')).toBeVisible();

  const summary = boundary.getByText('Every check passed', { exact: false });
  await expect(summary).toBeVisible();
  // Folded, not dropped — the split is one click away.
  await expect(boundary.getByRole('cell', { name: 'plan' })).toBeHidden();
  await summary.click();
  await expect(boundary.getByRole('cell', { name: 'plan' })).toBeVisible();
});
