import { test, expect, type Locator } from '@playwright/experimental-ct-react';
import { RunCard } from '~/features/sessions/components/RunCard';
import { TestProviders } from './harness/TestProviders';
import {
  assetUse,
  chatRun,
  factoryRunSummary,
  RUN_DURATION_MS,
  RUN_END,
  RUN_START,
} from './harness/runFixtures';

// Fixed clock: card geometry is asserted, so "now" must not drift.
const NOW = Date.parse(RUN_END) + 60_000;

/** `left: 26.3492%; width: 21.4496%` → the two numbers. */
async function barGeometry(bar: Locator): Promise<{ left: number; width: number }> {
  const style = (await bar.getAttribute('style')) ?? '';
  const left = Number(/left:\s*([\d.]+)%/.exec(style)?.[1]);
  const width = Number(/width:\s*([\d.]+)%/.exec(style)?.[1]);
  return { left, width };
}

test('a pipeline run reads as a run: id, workflow, request, lanes and telemetry', async ({
  mount,
  page,
}) => {
  await mount(
    <TestProviders>
      <RunCard session={factoryRunSummary()} now={NOW} />
    </TestProviders>,
  );

  await expect(page.getByText('factory-3f5a20b0')).toBeVisible();
  await expect(page.getByText('factory', { exact: true })).toBeVisible();
  await expect(page.getByText(/Add a subtract\(a, b\) function/)).toBeVisible();

  // One row per agent lane, labelled in the lane's own colour.
  for (const lane of ['plan', 'build', 'checks', 'review', 'document']) {
    await expect(page.getByTitle(lane, { exact: true })).toBeVisible();
  }

  // Footer: outcome chip, a dot per phase, and the start time.
  await expect(page.getByText('success')).toBeVisible();
  await expect(page.locator('[data-phase-dot]')).toHaveCount(5);
  await expect(page.locator('[data-phase-dot="passed"]')).toHaveCount(5);

  // Stat chips.
  await expect(page.getByText('$0.19')).toBeVisible();
  await expect(page.getByText('1m 54s active')).toBeVisible();
  await expect(page.getByText('899.9k')).toBeVisible();

  // The whole card opens the run.
  await expect(page.getByRole('link')).toHaveAttribute('href', '/sessions/factory-3f5a20b0');
});

test('phase bars sit at their real position on the run axis', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <RunCard session={factoryRunSummary()} now={NOW} />
    </TestProviders>,
  );

  // build starts 30_342 ms into a 115_155 ms run and lasts 24_700 ms.
  const build = await barGeometry(page.locator('[data-phase-bar="build"]'));
  expect(build.left).toBeCloseTo((30_342 / RUN_DURATION_MS) * 100, 1);
  expect(build.width).toBeCloseTo((24_700 / RUN_DURATION_MS) * 100, 1);

  // plan opens the run, hard against the left edge.
  const plan = await barGeometry(page.locator('[data-phase-bar="plan"]'));
  expect(plan.left).toBeLessThan(0.1);

  // checks lasts 87 ms — floored so it stays visible, and still inside the track.
  const checks = await barGeometry(page.locator('[data-phase-bar="checks"]'));
  expect(checks.width).toBe(0.75);
  expect(checks.left + checks.width).toBeLessThanOrEqual(100);

  // document is the last stage and ends with the run.
  const document = await barGeometry(page.locator('[data-phase-bar="document"]'));
  expect(document.left + document.width).toBeCloseTo(100, 0);
});

test('a chat session renders in the same grid, with its synthesized lanes', async ({
  mount,
  page,
}) => {
  const chat = chatRun();
  await mount(
    <TestProviders>
      <RunCard
        session={{ ...chat, phases: chat.phases.map((p) => ({ ...p })) }}
        now={Date.parse('2026-08-08T09:04:10.000Z')}
      />
    </TestProviders>,
  );

  // The uuid is cut to its first block; the full id stays in the tooltip.
  await expect(page.getByText('d70244ff')).toBeVisible();
  await expect(page.getByTitle('d70244ff-e3b3-4ee0-a615-12754b772de9')).toBeVisible();
  await expect(page.getByText('chat', { exact: true })).toBeVisible();
  await expect(page.getByText('redesign the sessions screen')).toBeVisible();
  await expect(page.getByText('running')).toBeVisible();
  await expect(page.getByText('Live')).toBeVisible();

  // A lane the API declared but no turn used still gets a row. Exact, because
  // the same run also carries an asset chip for the agent of that name.
  await expect(page.getByTitle('backend-developer', { exact: true })).toBeVisible();

  // Unknown cost/tokens are shown as unknown, never as zero.
  await expect(page.getByText('—')).toHaveCount(2);

  // The open turn runs to the leading edge of the axis.
  const turn2 = await barGeometry(page.locator('[data-phase-bar="turn 2"]'));
  expect(turn2.left + turn2.width).toBeCloseTo(100, 0);
});

test('a long run caps its phase dots instead of walking out of the card', async ({
  mount,
  page,
}) => {
  const long = factoryRunSummary({
    phases: Array.from({ length: 58 }, (_, i) => ({
      seq: i + 1,
      name: `turn ${i + 1}`,
      agent: 'main',
      // The failure is well past the cap: the counter has to carry it.
      status: i === 40 ? 'failed' : 'passed',
      started_at: RUN_START,
      duration_ms: 1_000,
    })),
  });

  await mount(
    <TestProviders>
      <RunCard session={long} now={NOW} />
    </TestProviders>,
  );

  await expect(page.locator('[data-phase-dot]')).toHaveCount(8);
  const overflow = page.locator('[data-phase-dots-overflow]');
  await expect(overflow).toHaveText('+50');
  await expect(overflow).toHaveAttribute('data-phase-dots-overflow', 'failed');

  // The dot row stays inside its card and clear of the timestamp beside it.
  const card = (await page.getByRole('link').boundingBox())!;
  const dots = (await page.locator('[aria-label="58 phases"]').boundingBox())!;
  const time = (await page.getByRole('time').boundingBox())!;
  expect(dots.x + dots.width).toBeLessThanOrEqual(card.x + card.width);
  expect(dots.x + dots.width).toBeLessThanOrEqual(time.x);
});

/**
 * The duplicate React key this guards against is only *warned* about by a
 * development React, and CT bundles the production one — so the assertion is on
 * the symptom the same rows produced on screen: one skill, two chips.
 */
test('a skill used on two lanes is one chip, and agents stay out of the row', async ({
  mount,
  page,
}) => {
  // The API counts per lane: backend-dev is recorded once for each lane that
  // loaded it.
  const run = factoryRunSummary({
    assets: [
      assetUse('skill', 'backend-dev', 2, 'main'),
      assetUse('skill', 'backend-dev', 3, 'backend-developer'),
      assetUse('agent', 'backend-developer', 4, 'main'),
      assetUse('skill', 'frontend-dev', 3, 'main'),
      assetUse('skill', 'alembic-heads', 2, 'backend-developer'),
      assetUse('skill', 'concise-comments', 2, 'main'),
      assetUse('skill', 'tdd', 1, 'qa-tester'),
    ],
  });

  await mount(
    <TestProviders>
      <RunCard session={run} now={NOW} />
    </TestProviders>,
  );

  const assets = page.getByLabel('Skills used');
  await expect(assets.getByText('backend-dev', { exact: true })).toHaveCount(1);
  // Both lanes' counts, on the one chip.
  await expect(assets.getByTitle('skill backend-dev — used 5×')).toBeVisible();
  await expect(assets.getByText('×5')).toBeVisible();

  // The lane chart above already names every agent, so the row never repeats one.
  await expect(assets.getByText('backend-developer', { exact: true })).toHaveCount(0);

  // Five skills after merging, so four chips and one hidden — not six and two.
  await expect(assets.getByText('+1 more')).toBeVisible();
  await expect(assets.getByText('tdd', { exact: true })).toHaveCount(0);
});

test('a run with no phases and no lanes still renders', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <RunCard session={factoryRunSummary({ phases: [], agents: [], title: null })} now={NOW} />
    </TestProviders>,
  );

  await expect(page.getByText('factory-3f5a20b0')).toBeVisible();
  // Falls back to the repo name for the request line and to one implicit lane.
  await expect(page.getByText('factory-e2e')).toBeVisible();
  await expect(page.getByText('session')).toBeVisible();
  await expect(page.locator('[data-phase-dot]')).toHaveCount(0);
});

test('the card is dated by its last activity, not by when it started', async ({ mount, page }) => {
  // The grid sorts on last activity, so a card dated by its start reads as
  // unsorted: this run began a day ago and answered a minute before `now`.
  // `ended_at` is from the life it already left; the run spoke again after it.
  const dayOld = factoryRunSummary({
    started_at: '2026-08-07T00:00:19.434Z',
    ended_at: '2026-08-07T00:02:14.589Z',
    last_event_at: RUN_END,
  });

  await mount(
    <TestProviders>
      <RunCard session={dayOld} now={NOW} />
    </TestProviders>,
  );

  // The label itself is relative to the real clock, so the machine-readable
  // attribute is what pins which instant the card chose.
  await expect(page.locator('time')).toHaveAttribute('datetime', RUN_END);
});

test('a chat waiting on its human still reads as live', async ({ mount, page }) => {
  // Ten minutes of silence is a person reading, not a dead run — the backend
  // says `running` for the same gap, and the dot has to agree with the chip.
  const waiting = chatRun({ ended_at: null, last_event_at: '2026-08-08T09:00:00.000Z' });

  await mount(
    <TestProviders>
      <RunCard session={waiting} now={Date.parse('2026-08-08T09:10:00.000Z')} />
    </TestProviders>,
  );

  await expect(page.getByText('Live')).toBeVisible();
});
