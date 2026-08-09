import { test, expect } from '@playwright/experimental-ct-react';
// Pure-logic tests (no mount): import relatively so they resolve in the Node worker.
import {
  buildTimeScale,
  contextUsedPct,
  formatCost,
  formatSpan,
  formatTokens,
  layoutLane,
  linearScale,
  placePoint,
  runWindow,
  scaleTicks,
} from '../../src/lib/timeline';
import { buildLanes, implicitLane, laneHueSat, laneTint } from '../../src/features/sessions/lanes';

// The reference run: 2026-08-08T00:00:19.434Z → 00:02:14.589Z, 115_155 ms.
const RUN = {
  started_at: '2026-08-08T00:00:19.434Z',
  last_event_at: '2026-08-08T00:02:14.589Z',
  ended_at: '2026-08-08T00:02:14.589Z',
};
const DURATION_MS = 115_155;
const NOW = Date.parse('2026-08-09T00:00:00.000Z');

test('a closed run measures itself between its own two stamps', () => {
  const axis = runWindow(RUN, NOW);
  expect(axis.startMs).toBe(Date.parse(RUN.started_at));
  expect(axis.endMs).toBe(Date.parse(RUN.ended_at));
  expect(axis.durationMs).toBe(DURATION_MS);
});

test('an open run grows towards now while live, and stops once it goes quiet', () => {
  const lastEvent = '2026-08-08T00:02:14.589Z';
  const justAfter = Date.parse(lastEvent) + 30_000;
  const live = runWindow({ ...RUN, ended_at: null, last_event_at: lastEvent }, justAfter);
  expect(live.endMs).toBe(justAfter);

  // Silent for a day: the axis is pinned to the last event, not to now.
  const stale = runWindow({ ...RUN, ended_at: null, last_event_at: lastEvent }, NOW);
  expect(stale.endMs).toBe(Date.parse(lastEvent));
});

test('a phase lands where it actually ran', () => {
  const axis = runWindow(RUN, NOW);
  const [plan, build] = layoutLane(linearScale(axis), [
    // plan starts within 35ms of the run: hard against the left edge.
    { started_at: '2026-08-08T00:00:19.469Z', duration_ms: 30_151 },
    // build: starts 30_342 ms in, runs 24_700 ms.
    { started_at: '2026-08-08T00:00:49.776Z', duration_ms: 24_700 },
  ]).spans;

  expect(plan.leftPct).toBeCloseTo(0.03, 1);
  expect(build.leftPct).toBeCloseTo((30_342 / DURATION_MS) * 100, 2);
  expect(build.widthPct).toBeCloseTo((24_700 / DURATION_MS) * 100, 2);
  expect(build.row).toBe(0);
  expect(build.instant).toBe(false);
});

test('a short phase keeps a clickable width without escaping the track', () => {
  const axis = runWindow(RUN, NOW);
  // checks: 87 ms — 0.08% of the run.
  const [checks] = layoutLane(
    linearScale(axis),
    [{ started_at: '2026-08-08T00:01:14.584Z', duration_ms: 87 }],
    {
      minWidthPct: 2,
    },
  ).spans;
  expect(checks.widthPct).toBe(2);
  expect(checks.leftPct + checks.widthPct).toBeLessThanOrEqual(100);

  // A phase that runs out with the run is nudged left rather than overflowing.
  const [last] = layoutLane(
    linearScale(axis),
    [{ started_at: '2026-08-08T00:02:13.589Z', duration_ms: 5_000 }],
    { minWidthPct: 5 },
  ).spans;
  expect(last.widthPct).toBe(5);
  expect(last.leftPct).toBe(95);
});

test('a phase nobody timed is a moment, not a bar inflated to the floor', () => {
  const axis = runWindow(RUN, NOW);
  const [marker] = layoutLane(
    linearScale(axis),
    [{ started_at: '2026-08-08T00:01:14.584Z', duration_ms: 0 }],
    {
      minWidthPct: 2,
    },
  ).spans;
  expect(marker.instant).toBe(true);
  expect(marker.widthPct).toBe(0);
});

test('a running phase extends to the leading edge of the window', () => {
  const axis = runWindow(RUN, NOW);
  const [running] = layoutLane(linearScale(axis), [
    { started_at: '2026-08-08T00:01:46.261Z', duration_ms: null, ended_at: null },
  ]).spans;
  expect(running.leftPct + running.widthPct).toBeCloseTo(100, 5);
  expect(running.truncated).toBe(false);
});

test('a turn whose end was never recorded is cut back to the next one on its lane', () => {
  const axis = runWindow(RUN, NOW);
  // The shape of the real `3fdd098b` bug: turn 11 stayed open and claimed the
  // rest of the run, so every later turn drew on top of it.
  const [leaked, next] = layoutLane(linearScale(axis), [
    { started_at: '2026-08-08T00:00:19.434Z', duration_ms: null, ended_at: null },
    { started_at: '2026-08-08T00:00:49.434Z', duration_ms: 24_700 },
  ]).spans;

  expect(leaked.truncated).toBe(true);
  expect(leaked.durationMs).toBe(30_000);
  expect(leaked.leftPct + leaked.widthPct).toBeLessThanOrEqual(next.leftPct + 0.001);
  // With the phantom span gone, both fit on one row.
  expect([leaked.row, next.row]).toEqual([0, 0]);
});

test('spans that would collide are packed into sub-rows, capped', () => {
  const axis = runWindow(RUN, NOW);
  // Four 1s phases inside two seconds: at a 2% floor each claims ~2.3s of axis.
  const at = (offsetMs: number) => ({
    started_at: new Date(Date.parse(RUN.started_at) + offsetMs).toISOString(),
    duration_ms: 1_000,
  });
  const dense = layoutLane(linearScale(axis), [at(0), at(500), at(1_000), at(1_500)], {
    minWidthPct: 2,
    maxRows: 3,
  });

  expect(dense.rows).toBe(3);
  expect(dense.spans.map((s) => s.row)).toEqual([0, 1, 2, 0]);

  // One row, and nothing is dropped, when the cap says so.
  const flat = layoutLane(linearScale(axis), [at(0), at(500), at(1_000)], {
    minWidthPct: 2,
    maxRows: 1,
  });
  expect(flat.rows).toBe(1);
  expect(flat.spans).toHaveLength(3);
});

test('a moment on the axis is a clamped percentage', () => {
  const axis = runWindow(RUN, NOW);
  expect(placePoint(axis, RUN.started_at)).toBe(0);
  expect(placePoint(axis, RUN.ended_at)).toBe(100);
  expect(placePoint(axis, '2020-01-01T00:00:00Z')).toBe(0);
  expect(placePoint(axis, '2030-01-01T00:00:00Z')).toBe(100);
});

test('axis ticks are round numbers, capped by the interval budget', () => {
  const axis = runWindow(RUN, NOW);
  expect(scaleTicks(linearScale(axis), 5).map((t) => t.label)).toEqual([
    '0s',
    '30s',
    '1m',
    '1m 30s',
  ]);
  // Every tick sits inside the track.
  for (const tick of scaleTicks(linearScale(axis), 5)) {
    expect(tick.atPct).toBeGreaterThanOrEqual(0);
    expect(tick.atPct).toBeLessThanOrEqual(100);
  }
  // A run of a few hours falls back to hour-sized steps.
  const hours = runWindow(
    { ...RUN, ended_at: '2026-08-08T04:00:19.434Z', last_event_at: '2026-08-08T04:00:19.434Z' },
    NOW,
  );
  expect(scaleTicks(linearScale(hours), 4).map((t) => t.label)).toEqual([
    '0s',
    '1h',
    '2h',
    '3h',
    '4h',
  ]);
});

test('a run that sat idle for hours spends its axis on the parts that ran', () => {
  // Two minutes of work, a nine-hour pause, two more minutes — the shape of
  // every long chat session, and unreadable on a linear axis.
  const start = Date.parse('2026-08-08T00:00:00.000Z');
  const hour = 3_600_000;
  const at = (offsetMs: number, durationMs: number) => ({
    started_at: new Date(start + offsetMs).toISOString(),
    duration_ms: durationMs,
  });
  const axis = runWindow(
    {
      started_at: new Date(start).toISOString(),
      ended_at: new Date(start + 9 * hour + 120_000).toISOString(),
      last_event_at: new Date(start + 9 * hour + 120_000).toISOString(),
    },
    NOW,
  );
  const scale = buildTimeScale(axis, [[at(0, 120_000), at(9 * hour, 120_000)]]);

  expect(scale.breaks).toHaveLength(1);
  expect(scale.breaks[0].skippedMs).toBe(9 * hour - 120_000);
  // Two equal stretches of work get equal width, and the nine hours between
  // them get the fixed band — not 99.6% of the chart.
  expect(scale.segments[0].widthPct).toBeCloseTo(scale.segments[1].widthPct, 5);
  expect(scale.breaks[0].widthPct).toBeLessThan(10);
  expect(scale.project(start)).toBe(0);
  expect(scale.project(start + 9 * hour + 120_000)).toBeCloseTo(100, 5);

  // The second stretch of work is now wide enough to hold a label.
  const [, second] = layoutLane(scale, [at(0, 120_000), at(9 * hour, 120_000)]).spans;
  expect(second.widthPct).toBeGreaterThan(40);
});

test('only the worst gaps are cut, and a run without them stays linear', () => {
  const start = Date.parse('2026-08-08T00:00:00.000Z');
  const minute = 60_000;
  const at = (offsetMs: number) => ({
    started_at: new Date(start + offsetMs).toISOString(),
    duration_ms: minute,
  });
  const axis = runWindow(
    {
      started_at: new Date(start).toISOString(),
      ended_at: new Date(start + 600 * minute).toISOString(),
      last_event_at: new Date(start + 600 * minute).toISOString(),
    },
    NOW,
  );

  // Six idle stretches, only the four longest worth cutting.
  const spans = [0, 20, 60, 130, 220, 330, 460].map((m) => at(m * minute));
  const scale = buildTimeScale(axis, [spans], { maxBreaks: 4 });
  expect(scale.breaks).toHaveLength(4);
  expect(scale.breaks.map((b) => b.skippedMs).sort((a, b) => a - b)[0]).toBeGreaterThan(
    60 * minute,
  );

  // Nothing idle for long enough: one segment, no bands, an ordinary axis.
  const busy = buildTimeScale(axis, [
    [{ started_at: new Date(start).toISOString(), duration_ms: 600 * minute }],
  ]);
  expect(busy.breaks).toHaveLength(0);
  expect(busy.segments).toHaveLength(1);
  expect(busy.segments[0].widthPct).toBe(100);
});

test('telemetry formats read as telemetry', () => {
  expect(formatCost(0.192431)).toBe('$0.1924');
  expect(formatCost(null)).toBe('—');
  expect(formatTokens(899_924)).toBe('899.9k');
  expect(formatTokens(1_110_000)).toBe('1.11M');
  expect(formatTokens(224_000)).toBe('224k');
  expect(formatTokens(842)).toBe('842');
  expect(formatTokens(null)).toBe('—');
  expect(formatSpan(87)).toBe('87ms');
  expect(formatSpan(24_700)).toBe('24s');
  expect(formatSpan(161_000)).toBe('2m 41s');
  expect(formatSpan(null)).toBe('—');
});

test('context is a percentage only when a window was reported', () => {
  expect(contextUsedPct(20_000, 200_000)).toBe(10);
  expect(contextUsedPct(20_000, null)).toBeNull();
  expect(contextUsedPct(null, 200_000)).toBeNull();
  expect(contextUsedPct(400_000, 200_000)).toBe(100);
});

test('lanes keep the API order and adopt phases nobody declared a lane for', () => {
  const agents = [implicitLane('plan'), implicitLane('build')];
  const phases = [
    { agent: 'plan', seq: 1 },
    { agent: 'build', seq: 2 },
    { agent: 'reviewer-nobody-declared', seq: 3 },
    { agent: null, seq: 4 },
  ];

  const lanes = buildLanes(agents, phases);
  expect(lanes.map((l) => l.lane.name)).toEqual([
    'plan',
    'build',
    'reviewer-nobody-declared',
    'unassigned',
  ]);
  expect(lanes[0].phases.map((p) => p.seq)).toEqual([1]);
  expect(lanes[3].phases.map((p) => p.seq)).toEqual([4]);
});

test('a lane keeps the hue the producer chose, and gets a stable one otherwise', () => {
  // #6aa9ff is a blue: the hue survives even though the lightness is re-picked.
  const fromApi = laneHueSat({ name: 'plan', color: '#6aa9ff' });
  expect(fromApi.hue).toBeGreaterThan(205);
  expect(fromApi.hue).toBeLessThan(225);

  // No colour reported: same name always yields the same hue, different names differ.
  expect(laneHueSat({ name: 'plan', color: null })).toEqual(laneHueSat({ name: 'plan' }));
  expect(laneHueSat({ name: 'plan' }).hue).not.toBe(laneHueSat({ name: 'document' }).hue);

  // Both themes are covered by every colour the chart draws with.
  const tint = laneTint({ name: 'plan', color: '#6aa9ff' });
  expect(tint.text).toContain('light-dark(');
  expect(tint.fill).toContain('/ 0.16');
});
