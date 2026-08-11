import { test, expect } from '@playwright/experimental-ct-react';
import { RunCard } from '~/features/sessions/components/RunCard';
import { SessionHeader } from '~/features/sessions/components/SessionHeader';
import { runDuration } from '~/lib/timeline';
import { runTitleMeta } from '~/features/sessions/runs';
import { TestProviders } from './harness/TestProviders';
import { chatRun, factoryRunSummary, RUN_END } from './harness/runFixtures';

/**
 * The four ways the run cards used to mislead: a doubled currency symbol, a
 * wall-clock duration presented as the duration, a dead run wearing "Running",
 * and an opaque id where the request should be.
 */

const NOW = Date.parse(RUN_END) + 60_000;

function chatSummary(overrides = {}) {
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

test('the cost is written once, not twice', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <RunCard session={factoryRunSummary({ cost_usd: 0.2716 })} now={NOW} />
    </TestProviders>,
  );

  const cost = page.getByTitle('Cost');
  // The formatter emits the `$`; pairing it with a dollar icon read "$ $0.2716".
  await expect(cost).toHaveText('Cost: $0.27');
  await expect(page.getByText('$$')).toHaveCount(0);
  await expect(cost.locator('svg')).toHaveCount(0);

  // Every other chip keeps its icon — only the currency duplicated a glyph.
  await expect(page.getByTitle('Total tokens').locator('svg')).toHaveCount(1);
});

test('an unknown cost gets the icon back, because "—" carries no symbol', async ({
  mount,
  page,
}) => {
  await mount(
    <TestProviders>
      <RunCard session={factoryRunSummary({ cost_usd: null })} now={NOW} />
    </TestProviders>,
  );

  const cost = page.getByTitle('Cost');
  await expect(cost).toHaveText('Cost: —');
  // Without a value there is no `$` to duplicate, so the chip stays identifiable.
  await expect(cost.locator('svg')).toHaveCount(1);
});

test('duration leads with working time and keeps the wall clock beside it', async ({
  mount,
  page,
}) => {
  // A run that worked for 24 seconds and then sat on a closed laptop overnight.
  const idle = factoryRunSummary({ active_ms: 24_000, wall_ms: 123_480_000 });

  await mount(
    <TestProviders>
      <RunCard session={idle} now={NOW} />
    </TestProviders>,
  );

  const duration = page.getByText('24s active');
  await expect(duration).toBeVisible();
  // The wall clock survives, muted and labelled — never as *the* duration.
  await expect(page.getByText('· 34h 18m elapsed')).toBeVisible();
  await expect(page.getByTitle('24s of actual work, over 34h 18m of wall clock')).toBeVisible();
});

test('a run whose clock agrees with its work says so once', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <RunCard session={factoryRunSummary({ active_ms: 114_620, wall_ms: 115_155 })} now={NOW} />
    </TestProviders>,
  );

  await expect(page.getByText('1m 54s active')).toBeVisible();
  await expect(page.getByText(/elapsed/)).toHaveCount(0);
});

test('runDuration only mentions the wall clock once it disagrees', () => {
  expect(runDuration(24_000, 123_480_000)).toEqual({
    active: '24s',
    elapsed: '34h 18m',
    idle: true,
    label: '24s of actual work, over 34h 18m of wall clock',
  });

  // Half a second apart: the clock adds nothing.
  expect(runDuration(115_000, 115_500).idle).toBe(false);
  // The factory sums its stages, which can exceed the run's own window.
  expect(runDuration(3_042_310, 1_962_489).idle).toBe(false);
  expect(runDuration(null, null)).toMatchObject({ active: '—', elapsed: '—', idle: false });
});

test('an abandoned run is its own chip, neither a failure nor still running', async ({
  mount,
  page,
}) => {
  await mount(
    <TestProviders>
      <RunCard
        session={factoryRunSummary({ status: 'abandoned', ended_at: null })}
        // Long past the live window: an open run this quiet is the whole point.
        now={Date.parse(RUN_END) + 60 * 60_000}
      />
    </TestProviders>,
  );

  const chip = page.getByText('abandoned');
  await expect(chip).toBeVisible();
  await expect(page.getByText('Live')).toHaveCount(0);
  // Exact: `getByText` is a case-insensitive substring match, and a run titled
  // "You are running a SIMULATION…" would otherwise satisfy this.
  await expect(page.getByText('running', { exact: true })).toHaveCount(0);

  // Muted, not the error tone the `failed` chip wears.
  await expect(chip).toHaveClass(/bg-muted/);
  await expect(chip).not.toHaveClass(/red/);

  // And it explains itself, because "abandoned" is derived from silence.
  await expect(chip).toHaveAttribute('title', /SessionEnd hook dies with the process/);
});

test('the title is the headline and the id is the footnote', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <RunCard session={factoryRunSummary()} now={NOW} />
    </TestProviders>,
  );

  const title = page.getByText(/Add a subtract\(a, b\) function/);
  const id = page.getByText('factory-3f5a20b0');
  await expect(title).toBeVisible();
  await expect(id).toBeVisible();

  // The request is set in the card's largest type; the id stays small and mono.
  await expect(title).toHaveClass(/font-semibold/);
  await expect(id.locator('..')).toHaveClass(/font-mono/);
  await expect(id.locator('..')).toHaveClass(/text-muted-foreground/);

  // A factory title says where it came from.
  await expect(page.getByText('pipeline request')).toBeVisible();
});

test('a cwd title reads as the absence of one', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <RunCard session={factoryRunSummary({ title: null, title_source: 'cwd' })} now={NOW} />
    </TestProviders>,
  );

  const fallback = page.getByText('factory-e2e');
  await expect(fallback).toBeVisible();
  // Italic, muted, mono — a folder name, not a request.
  await expect(fallback).toHaveClass(/italic/);
  await expect(fallback).toHaveClass(/text-muted-foreground/);
  await expect(fallback).toHaveAttribute('title', 'Untitled run — showing factory-e2e');
});

test('a pipeline stage announces its provenance', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <RunCard
        session={chatSummary({
          title: 'build stage · factory-5e3b0f90',
          title_source: 'provenance',
          parent_session_id: 'factory-5e3b0f90',
        })}
        now={Date.parse('2026-08-08T09:04:10.000Z')}
      />
    </TestProviders>,
  );

  await expect(page.getByText('build stage · factory-5e3b0f90')).toBeVisible();
  await expect(page.getByText('pipeline stage')).toBeVisible();
});

test('titleMeta ranks the stored title over the folder, and marks the fallback', () => {
  const run = factoryRunSummary();
  expect(runTitleMeta(run)).toMatchObject({
    source: 'factory',
    weak: false,
    hint: 'pipeline request',
  });
  expect(runTitleMeta({ ...run, title_source: 'prompt' })).toMatchObject({
    weak: false,
    hint: null,
  });
  // An empty title is no title, whatever the source column claims.
  expect(runTitleMeta({ ...run, title: '   ', title_source: 'prompt' })).toMatchObject({
    text: 'factory-e2e',
    source: 'cwd',
    weak: true,
  });
});

test('a run that launched stages says how many', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <RunCard session={factoryRunSummary({ child_count: 4 })} now={NOW} />
    </TestProviders>,
  );
  await expect(page.getByText('4 stage runs')).toBeVisible();
  await expect(page.getByText('4 stage runs')).toHaveAttribute(
    'title',
    /headless run per pipeline stage/,
  );
});

test('a run that launched nothing says nothing', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <RunCard session={factoryRunSummary({ child_count: 0 })} now={NOW} />
    </TestProviders>,
  );
  await expect(page.getByText(/stage runs?/)).toHaveCount(0);
});

test('a stage run offers the way back up to its parent', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <SessionHeader
        session={chatRun({
          title: 'review stage · factory-5e3b0f90',
          title_source: 'provenance',
          parent_session_id: 'factory-5e3b0f90',
          status: 'abandoned',
        })}
      />
    </TestProviders>,
  );

  await expect(page.getByRole('link', { name: 'parent run' })).toHaveAttribute(
    'href',
    '/sessions/factory-5e3b0f90',
  );
  await expect(page.getByText('pipeline stage')).toBeVisible();
});
