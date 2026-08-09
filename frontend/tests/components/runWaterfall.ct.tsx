import { test, expect, type Locator } from '@playwright/experimental-ct-react';
import { RunWaterfall } from '~/features/sessions/components/RunWaterfall';
import { TestProviders } from './harness/TestProviders';
import {
  chatRun,
  denseChatRun,
  DENSE_RUN_END,
  factoryRun,
  RUN_DURATION_MS,
  RUN_END,
  toolCall,
} from './harness/runFixtures';

const NOW = Date.parse(RUN_END) + 60_000;

async function blockGeometry(block: Locator): Promise<{ left: number; width: number }> {
  const style = (await block.getAttribute('style')) ?? '';
  return {
    left: Number(/left:\s*([\d.]+)%/.exec(style)?.[1]),
    width: Number(/width:\s*([\d.]+)%/.exec(style)?.[1]),
  };
}

test('one row per lane, each with its model and context bar', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <RunWaterfall
        session={factoryRun()}
        events={[]}
        now={NOW}
        selectedPhaseId={null}
        onSelectPhase={() => {}}
      />
    </TestProviders>,
  );

  // The shared axis, scaled to a 1m 55s run.
  await expect(page.getByText('0s', { exact: true })).toBeVisible();
  await expect(page.getByText('1m 30s')).toBeVisible();

  for (const lane of ['plan', 'build', 'checks', 'review', 'document']) {
    await expect(page.getByTitle(lane, { exact: true })).toBeVisible();
  }
  // Agent lanes name their model; the `code` lane has none, so it names its kind.
  await expect(page.getByTitle('haiku').first()).toBeVisible();
  await expect(page.getByTitle('code')).toBeVisible();

  // Only the lane that reported a window gets a context bar: 20k of 200k.
  await expect(page.getByText('Context')).toHaveCount(1);
  await expect(page.getByText('10%')).toBeVisible();
});

test('phase blocks are placed and sized by real time, and stay clickable when tiny', async ({
  mount,
  page,
}) => {
  await mount(
    <TestProviders>
      <RunWaterfall
        session={factoryRun()}
        events={[]}
        now={NOW}
        selectedPhaseId={null}
        onSelectPhase={() => {}}
      />
    </TestProviders>,
  );

  const build = await blockGeometry(page.getByRole('button', { name: 'Phase build' }));
  expect(build.left).toBeCloseTo((30_342 / RUN_DURATION_MS) * 100, 1);
  expect(build.width).toBeCloseTo((24_700 / RUN_DURATION_MS) * 100, 1);

  // 87 ms is 0.08% of the run: floored so it stays clickable, and the floor is
  // the measured pixel minimum, which is what the packer reserves for it too.
  const checks = page.getByRole('button', { name: 'Phase checks' });
  expect((await blockGeometry(checks)).width).toBeGreaterThan(0.08);
  expect((await checks.boundingBox())!.width).toBeGreaterThanOrEqual(10);
  await checks.click();

  // Each block carries its own duration, right-aligned.
  await expect(page.getByRole('button', { name: 'Phase checks' })).toContainText('87ms');
  await expect(page.getByRole('button', { name: 'Phase build' })).toContainText('24s');
  await expect(page.getByRole('button', { name: 'Phase plan' })).toContainText(
    'plan stage description',
  );
});

test('tool calls are ticks along the phase that produced them', async ({ mount, page }) => {
  const run = factoryRun();
  const planId = run.phases[0].id;
  const events = [
    toolCall(1, planId, '2026-08-08T00:00:22.000Z'),
    toolCall(2, planId, '2026-08-08T00:00:30.000Z'),
    toolCall(3, planId, '2026-08-08T00:00:45.000Z'),
    // A result row is not a call of its own, and never becomes a tick.
    { ...toolCall(4, planId, '2026-08-08T00:00:46.000Z'), tool_name: 'tool_result' },
  ];

  await mount(
    <TestProviders>
      <RunWaterfall
        session={run}
        events={events}
        now={NOW}
        selectedPhaseId={null}
        onSelectPhase={() => {}}
      />
    </TestProviders>,
  );

  await expect(page.getByLabel('3 tool calls')).toBeAttached();
  await expect(
    page.getByRole('button', { name: 'Phase build' }).getByLabel(/tool calls/),
  ).toHaveCount(0);
});

test('a session with no lanes and no phases falls back to one implicit lane', async ({
  mount,
  page,
}) => {
  const bare = chatRun({ phases: [], agents: [], model: 'claude-opus-4' });
  await mount(
    <TestProviders>
      <RunWaterfall
        session={bare}
        events={[toolCall(1, 0, '2026-08-08T09:02:00.000Z')]}
        now={Date.parse('2026-08-08T09:04:10.000Z')}
        selectedPhaseId={null}
        onSelectPhase={() => {}}
      />
    </TestProviders>,
  );

  await expect(page.getByText('claude-opus-4')).toBeVisible();
  await expect(page.getByText('no phases reported')).toBeVisible();
  await expect(page.getByRole('button')).toHaveCount(0);
});

test('a turn that never closed is cut back to the next one, not left covering the run', async ({
  mount,
  page,
}) => {
  await mount(
    <TestProviders>
      <RunWaterfall
        session={denseChatRun()}
        events={[]}
        now={Date.parse(DENSE_RUN_END) + 60_000}
        selectedPhaseId={null}
        onSelectPhase={() => {}}
      />
    </TestProviders>,
  );

  // Opens at minute 30 of 600 and is cut where the next turn starts at minute
  // 90 — an hour, not the nine and a half it would claim by running to the edge.
  // Asserted against its successor rather than a fixed percentage: the axis
  // collapses idle time, so where minute 90 lands is the scale's business.
  const main = page.locator('[data-lane="main"]');
  const leaked = main.getByRole('button', { name: 'Phase turn 2' });
  const next = main.getByRole('button', { name: 'Phase turn 3' });
  const { left, width } = await blockGeometry(leaked);

  expect(left + width).toBeCloseTo((await blockGeometry(next)).left, 1);
  expect(left + width).toBeLessThan(100);
  await expect(leaked).toHaveClass(/border-dashed/);
  await expect(leaked).toHaveAttribute('title', /end not recorded/);
  // 1h of the run's 10h, drawn as a bar you can read rather than a sliver.
  await expect(leaked).toContainText('1h');
});

test('spawns nobody timed are markers, and colliding phases get their own sub-row', async ({
  mount,
  page,
}) => {
  await mount(
    <TestProviders>
      <RunWaterfall
        session={denseChatRun()}
        events={[]}
        now={Date.parse(DENSE_RUN_END) + 60_000}
        selectedPhaseId={null}
        onSelectPhase={() => {}}
      />
    </TestProviders>,
  );

  const subagent = page.locator('[data-lane="subagent"]');

  // The subagent lane is three moments — no bar claims a length it never had.
  await expect(page.locator('[data-phase-marker]')).toHaveCount(3);
  await expect(page.getByText('start times only')).toBeVisible();
  await expect(subagent.getByRole('button', { name: 'Phase turn 1' })).toHaveAttribute(
    'title',
    /duration not recorded/,
  );

  // Two minutes apart on a ten-hour axis: stacked, not painted on each other.
  const tops = await subagent
    .locator('[data-phase-marker]')
    .evaluateAll((els) => els.map((el) => (el as HTMLElement).style.top));
  expect(new Set(tops).size).toBeGreaterThan(1);
});

test('no two phases in a lane are drawn on top of each other', async ({ mount, page }) => {
  // The invariant the whole layout exists for: the floor the packer reserves is
  // the floor that renders, so blocks that clear each other in percent clear
  // each other in pixels too.
  await mount(
    <TestProviders>
      <RunWaterfall
        session={denseChatRun()}
        events={[]}
        now={Date.parse(DENSE_RUN_END) + 60_000}
        selectedPhaseId={null}
        onSelectPhase={() => {}}
      />
    </TestProviders>,
  );

  for (const lane of ['main', 'subagent']) {
    const boxes = await page
      .locator(`[data-lane="${lane}"] button`)
      .evaluateAll((els) =>
        els.map((el) => el.getBoundingClientRect()).map((r) => ({ x: r.x, w: r.width, y: r.y })),
      );

    for (const [i, a] of boxes.entries()) {
      for (const b of boxes.slice(i + 1)) {
        if (a.y !== b.y) continue; // different sub-rows never collide
        const overlaps = a.x < b.x + b.w && b.x < a.x + a.w;
        expect(overlaps, `${lane}: blocks ${i} and ${boxes.indexOf(b)} overlap`).toBe(false);
      }
    }
  }
});

test('the selected phase is marked pressed', async ({ mount, page }) => {
  const run = factoryRun();
  await mount(
    <TestProviders>
      <RunWaterfall
        session={run}
        events={[]}
        now={NOW}
        selectedPhaseId={run.phases[3].id}
        onSelectPhase={() => {}}
      />
    </TestProviders>,
  );

  await expect(page.getByRole('button', { name: 'Phase review' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await expect(page.getByRole('button', { name: 'Phase plan' })).toHaveAttribute(
    'aria-pressed',
    'false',
  );
});
