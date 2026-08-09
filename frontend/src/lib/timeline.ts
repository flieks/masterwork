import { formatDuration } from './datetime';

/**
 * Pure geometry for run timelines: turn absolute timestamps into percentages
 * along a run's own time axis. Kept free of React and of the generated API
 * types so it can be unit-tested on its own.
 */

/** A run with no measurable span still gets a usable axis. */
const MIN_WINDOW_MS = 1_000;

/** An open run silent for longer than this stops stretching its axis to now. */
export const LIVE_WINDOW_MS = 2 * 60 * 1000;

/** Nice round tick intervals, seconds → hours. */
const TICK_STEPS_MS = [
  1_000, 2_000, 5_000, 10_000, 15_000, 30_000, 60_000, 120_000, 300_000, 600_000, 900_000,
  1_800_000, 3_600_000, 7_200_000, 21_600_000, 43_200_000, 86_400_000,
];

export interface RunWindow {
  startMs: number;
  endMs: number;
  /** Never below MIN_WINDOW_MS, so a division is always safe. */
  durationMs: number;
}

export interface Placement {
  leftPct: number;
  widthPct: number;
}

export interface AxisTick {
  atMs: number;
  atPct: number;
  label: string;
}

/** The shape of a run this module needs — structural, so any session fits. */
export interface RunLike {
  started_at: string;
  last_event_at: string;
  ended_at: string | null;
}

/** The shape of a phase this module needs. `duration_ms` wins over `ended_at`. */
export interface SpanLike {
  started_at: string;
  duration_ms?: number | null;
  ended_at?: string | null;
}

function epoch(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? null : t;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), Math.max(min, max));
}

/**
 * The window every bar on a run's chart is measured against. A closed run ends
 * where it ended; an open one that is still firing hooks grows towards `now`,
 * while one that went quiet is pinned to its last event so a dead session's
 * axis does not creep while you look at it.
 */
export function runWindow(run: RunLike, now: number, liveWindowMs = LIVE_WINDOW_MS): RunWindow {
  const startMs = epoch(run.started_at) ?? now;
  const lastMs = epoch(run.last_event_at) ?? startMs;
  const endedMs = epoch(run.ended_at);
  const openEnd = now - lastMs < liveWindowMs ? Math.max(lastMs, now) : lastMs;
  const endMs = Math.max(endedMs ?? openEnd, startMs);
  return { startMs, endMs, durationMs: Math.max(endMs - startMs, MIN_WINDOW_MS) };
}

/** End of a span: its reported duration, else its end stamp, else "still running". */
function spanEnd(axis: RunWindow, span: SpanLike, startMs: number): number {
  if (typeof span.duration_ms === 'number' && span.duration_ms >= 0) {
    return startMs + span.duration_ms;
  }
  const ended = epoch(span.ended_at);
  if (ended !== null) return ended;
  // Running: extends to the leading edge of the window.
  return axis.endMs;
}

/** Place a single moment (an event) on the axis, as a percentage. */
export function placePoint(axis: RunWindow, iso: string): number {
  const at = epoch(iso);
  if (at === null) return 0;
  return clamp(((at - axis.startMs) / axis.durationMs) * 100, 0, 100);
}

/** True when a span reported neither a length nor an end — it is still open. */
function isOpen(span: SpanLike): boolean {
  return (
    !(typeof span.duration_ms === 'number' && span.duration_ms >= 0) &&
    epoch(span.ended_at) === null
  );
}

interface ResolvedSpan {
  startMs: number;
  endMs: number;
  durationMs: number;
  instant: boolean;
  truncated: boolean;
}

/**
 * One lane's spans as real instants on the clock — no percentages yet.
 *
 * The clamp lives here because two callers need the same answer: the scale, to
 * know which stretches of the run were occupied, and the layout, to draw them.
 * An unclosed span is cut back to whatever started after it on the same lane;
 * without that a `main` turn whose `Stop` was lost claims the rest of the run
 * and there is no idle time left to collapse.
 */
function resolveSpans(axis: RunWindow, spans: SpanLike[]): ResolvedSpan[] {
  const starts = spans.map((span) =>
    clamp(epoch(span.started_at) ?? axis.startMs, axis.startMs, axis.endMs),
  );
  const ascending = [...starts].sort((a, b) => a - b);

  return spans.map((span, i) => {
    const startMs = starts[i];
    // The lane's own next start, not the next by index: `seq` is session-wide,
    // and a producer may hand its phases back in any order.
    const successor = ascending.find((at) => at > startMs);
    const rawEnd = spanEnd(axis, span, startMs);
    const truncated = isOpen(span) && successor !== undefined && successor < rawEnd;
    const endMs = clamp(truncated ? successor : rawEnd, startMs, axis.endMs);
    const durationMs = endMs - startMs;
    return { startMs, endMs, durationMs, instant: durationMs <= 0, truncated };
  });
}

/**
 * Idle longer than this is dead space, not part of the shape of the run.
 * Above the backend's 60s `ACTIVE_GAP` on purpose: a minute between two tool
 * calls is a person reading, and cutting it would fragment a normal turn.
 */
export const COLLAPSIBLE_GAP_MS = 5 * 60_000;

/** What one collapsed gap costs on the axis, however many hours it really was. */
const BREAK_PCT = 4;
/** Breaks never take more of the chart than the work does. */
const MAX_BREAK_BUDGET_PCT = 24;

/**
 * How many gaps are worth cutting. A long session pauses dozens of times, and
 * cutting all of them turns the axis into a row of hatching with the work in
 * between — the opposite of the point. Only the biggest few are dead space
 * worth the reader's attention; the rest stay inside a segment, to scale.
 */
const MAX_BREAKS = 4;

export interface ScaleSegment {
  startMs: number;
  endMs: number;
  leftPct: number;
  widthPct: number;
}

export interface ScaleBreak extends ScaleSegment {
  /** Real time the band stands in for. */
  skippedMs: number;
}

export interface TimeScale {
  /** The run's own window — what the scale is a re-reading of. */
  window: RunWindow;
  /** Stretches that had something running, in order. */
  segments: ScaleSegment[];
  /** The idle stretches between them, each squeezed to a fixed band. */
  breaks: ScaleBreak[];
  /** An instant on the clock → a percentage along the axis. */
  project(atMs: number): number;
  activeMs: number;
  skippedMs: number;
}

/**
 * A time axis that spends its width on the parts of the run that did something.
 *
 * A 36-hour session with two hours of work draws both in the same picture: on a
 * linear axis the work is a thumbnail at one end and 34 hours of blank is the
 * chart. Here every idle stretch over `gapMs` becomes a fixed narrow band
 * labelled with what it swallowed, and the width it gives up is handed to the
 * stretches that were busy. A run with no long idle gets an ordinary linear
 * axis — the piecewise scale collapses to one segment on its own.
 */
export function buildTimeScale(
  axis: RunWindow,
  lanes: SpanLike[][],
  {
    gapMs = COLLAPSIBLE_GAP_MS,
    breakPct = BREAK_PCT,
    maxBreaks = MAX_BREAKS,
  }: { gapMs?: number; breakPct?: number; maxBreaks?: number } = {},
): TimeScale {
  const occupied: Array<[number, number]> = [];
  for (const spans of lanes) {
    for (const span of resolveSpans(axis, spans)) occupied.push([span.startMs, span.endMs]);
  }
  // The run's own ends are moments too: the axis has to start at 0 and reach
  // the leading edge even when nothing was running at either.
  occupied.push([axis.startMs, axis.startMs], [axis.endMs, axis.endMs]);
  occupied.sort((a, b) => a[0] - b[0]);

  // Every stretch with nothing in it, longest first — only the worst are cut.
  const busy: Array<[number, number]> = [];
  for (const [start, end] of occupied) {
    const last = busy[busy.length - 1];
    if (last && start <= last[1]) last[1] = Math.max(last[1], end);
    else busy.push([start, end]);
  }
  const cut = new Set(
    busy
      .slice(1)
      .map((band, i) => ({ at: i + 1, gap: band[0] - busy[i][1] }))
      .filter(({ gap }) => gap > gapMs)
      .sort((a, b) => b.gap - a.gap)
      .slice(0, Math.max(maxBreaks, 0))
      .map(({ at }) => at),
  );

  const merged: Array<[number, number]> = [];
  busy.forEach((band, i) => {
    if (i > 0 && !cut.has(i))
      merged[merged.length - 1][1] = Math.max(merged[merged.length - 1][1], band[1]);
    else merged.push([...band] as [number, number]);
  });

  const breakCount = merged.length - 1;
  const budget = Math.min(breakPct * breakCount, MAX_BREAK_BUDGET_PCT);
  const perBreak = breakCount > 0 ? budget / breakCount : 0;
  const usable = 100 - perBreak * breakCount;
  const activeMs = merged.reduce((sum, [start, end]) => sum + (end - start), 0);

  const segments: ScaleSegment[] = [];
  const breaks: ScaleBreak[] = [];
  /** Segments and breaks interleaved, so a lookup is one ordered walk. */
  const bands: ScaleSegment[] = [];
  let left = 0;

  merged.forEach(([startMs, endMs], i) => {
    // All-instant lanes have no measurable active time; share the width evenly
    // rather than divide by zero and stack every marker on the left edge.
    const share = activeMs > 0 ? (endMs - startMs) / activeMs : 1 / merged.length;
    const widthPct = usable * share;
    const segment = { startMs, endMs, leftPct: left, widthPct };
    segments.push(segment);
    bands.push(segment);
    left += widthPct;

    const next = merged[i + 1];
    if (!next) return;
    const gap = {
      startMs: endMs,
      endMs: next[0],
      leftPct: left,
      widthPct: perBreak,
      skippedMs: next[0] - endMs,
    };
    breaks.push(gap);
    bands.push(gap);
    left += perBreak;
  });

  const skippedMs = breaks.reduce((sum, gap) => sum + gap.skippedMs, 0);

  function project(atMs: number): number {
    const at = clamp(atMs, axis.startMs, axis.endMs);
    const band = bands.find((b) => at <= b.endMs) ?? bands[bands.length - 1];
    return band ? within(band, at) : 0;
  }

  return { window: axis, segments, breaks, project, activeMs, skippedMs };
}

function within(band: ScaleSegment, atMs: number): number {
  const span = band.endMs - band.startMs;
  if (span <= 0) return band.leftPct;
  const at = clamp(atMs, band.startMs, band.endMs);
  return band.leftPct + ((at - band.startMs) / span) * band.widthPct;
}

/** A scale for a run whose gaps are not worth collapsing — one linear segment. */
export function linearScale(axis: RunWindow): TimeScale {
  return buildTimeScale(axis, [], { gapMs: Number.POSITIVE_INFINITY });
}

export interface LaneSpan extends Placement {
  /** Sub-row this span was packed into; 0 unless something collided with it. */
  row: number;
  /** Length after an unclosed span is cut back to its successor. */
  durationMs: number;
  /** Nothing timed this span — draw a moment, not a bar. */
  instant: boolean;
  /** The open end was cut short because the lane moved on without it. */
  truncated: boolean;
}

export interface LaneLayout {
  spans: LaneSpan[];
  /** How many sub-rows the lane needs — at least 1, even when empty. */
  rows: number;
  /** The row cap was reached and something had to be doubled up anyway. */
  crowded: boolean;
}

export interface LaneLayoutOptions {
  /** Floor for a bar's width, so a short span stays visible and clickable. */
  minWidthPct?: number;
  /** Ceiling on sub-rows; past it the least-overlapping row is reused. */
  maxRows?: number;
}

/**
 * Lay out one lane's spans against a scale.
 *
 * Three things a plain percentage-of-the-window cannot do. An unclosed span is
 * cut back to whatever started after it — a `main` turn whose `Stop` hook was
 * lost would otherwise claim the rest of the run and sit under every later turn.
 * A span nobody timed becomes a marked instant rather than a bar inflated to
 * the width floor. And spans that would still collide are packed into sub-rows,
 * because a lane that paints them on top of each other reads as one phase.
 */
export function layoutLane(
  scale: TimeScale,
  spans: SpanLike[],
  { minWidthPct = 0, maxRows = 4 }: LaneLayoutOptions = {},
): LaneLayout {
  const placed: LaneSpan[] = resolveSpans(scale.window, spans).map((span) => {
    const startPct = scale.project(span.startMs);
    const widthPct = span.instant
      ? 0
      : clamp(Math.max(scale.project(span.endMs) - startPct, minWidthPct), 0, 100);
    const leftPct = clamp(startPct, 0, 100 - widthPct);
    return {
      leftPct,
      widthPct,
      row: 0,
      durationMs: span.durationMs,
      instant: span.instant,
      truncated: span.truncated,
    };
  });

  const crowded = packRows(placed, minWidthPct, maxRows);
  return {
    spans: placed,
    rows: placed.reduce((max, s) => Math.max(max, s.row + 1), 1),
    crowded,
  };
}

/**
 * First-fit by start time. An instant has no width of its own, so it reserves
 * the same floor a bar does — two markers a second apart still need two rows.
 * Returns whether the cap forced anything to share a row it did not fit in.
 */
function packRows(placed: LaneSpan[], minWidthPct: number, maxRows: number): boolean {
  const rowEnds: number[] = [];
  const order = placed.map((_, i) => i).sort((a, b) => placed[a].leftPct - placed[b].leftPct);
  let crowded = false;

  for (const i of order) {
    const span = placed[i];
    const end = span.leftPct + Math.max(span.widthPct, minWidthPct);
    let row = rowEnds.findIndex((at) => at <= span.leftPct);
    if (row === -1) {
      if (rowEnds.length < Math.max(maxRows, 1)) {
        row = rowEnds.length;
        rowEnds.push(end);
      } else {
        // Out of rows: the one that frees up soonest overlaps least.
        row = rowEnds.indexOf(Math.min(...rowEnds));
        rowEnds[row] = Math.max(rowEnds[row], end);
        crowded = true;
      }
    } else {
      rowEnds[row] = end;
    }
    span.row = row;
  }
  return crowded;
}

/** Two labels closer than this would collide; the later one is dropped. */
const MIN_TICK_GAP_PCT = 9;

/**
 * Labels for a scale rather than a duration. Every segment is labelled where it
 * starts — the number after a break is the one a reader needs — and gets round
 * interior ticks in proportion to the width it was given.
 */
export function scaleTicks(
  scale: TimeScale,
  maxIntervals = 5,
  minGapPct = MIN_TICK_GAP_PCT,
): AxisTick[] {
  const ticks: AxisTick[] = [];
  const push = (atMs: number, atPct: number) => {
    const elapsed = atMs - scale.window.startMs;
    if (ticks.some((t) => Math.abs(t.atPct - atPct) < minGapPct)) return;
    ticks.push({ atMs: elapsed, atPct, label: formatDuration(elapsed / 1000) });
  };

  for (const segment of scale.segments) {
    push(segment.startMs, segment.leftPct);

    const span = segment.endMs - segment.startMs;
    const budget = Math.max(1, Math.round((segment.widthPct / 100) * maxIntervals));
    if (span <= 0) continue;
    const step = TICK_STEPS_MS.find((s) => span / s <= budget);
    if (step === undefined) continue;

    // Round numbers measured from the run's start, so the labels read as one
    // clock even though the axis between them is not to scale.
    const first = Math.ceil((segment.startMs - scale.window.startMs) / step) * step;
    for (let at = first; at + scale.window.startMs <= segment.endMs; at += step) {
      push(at + scale.window.startMs, scale.project(at + scale.window.startMs));
    }
  }
  return ticks;
}

/** "87ms", "24s", "2m 41s" — a span, not a clock. */
export function formatSpan(ms: number | null | undefined): string {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms < 0) return '—';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return formatDuration(ms / 1000);
}

/** Below this the wall clock says nothing the active time didn't — don't mention it. */
const IDLE_NOTICE_MS = 60_000;

export interface RunDuration {
  /** Time actually spent working. The headline number. */
  active: string;
  /** First event to last, idle laptop included. */
  elapsed: string;
  /** True when the run sat idle long enough for the two to disagree. */
  idle: boolean;
  /** Both numbers, spelled out — for a tooltip. */
  label: string;
}

/**
 * How long a run took. `active_ms` leads because `wall_ms` measures the clock,
 * and a closed laptop turns 24 seconds of work into a 34-hour session; the wall
 * clock is only worth showing once it disagrees.
 */
export function runDuration(
  activeMs: number | null | undefined,
  wallMs: number | null | undefined,
): RunDuration {
  const active = formatSpan(activeMs);
  const elapsed = formatSpan(wallMs);
  const idle =
    typeof activeMs === 'number' &&
    typeof wallMs === 'number' &&
    wallMs - activeMs >= IDLE_NOTICE_MS;
  return {
    active,
    elapsed,
    idle,
    label: idle
      ? `${active} of actual work, over ${elapsed} of wall clock`
      : `${active} of actual work`,
  };
}

function trimZeros(value: string): string {
  return value.includes('.') ? value.replace(/\.?0+$/, '') : value;
}

/** Token counts the way a telemetry panel writes them: `899.9k`, `1.11M`. */
export function formatTokens(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  const abs = Math.abs(value);
  if (abs < 1_000) return String(Math.round(value));
  if (abs < 1_000_000) return `${trimZeros((value / 1_000).toFixed(1))}k`;
  return `${trimZeros((value / 1_000_000).toFixed(2))}M`;
}

/** `$0.1924` — four decimals, because a run often costs less than a cent. */
export function formatCost(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return `$${value.toFixed(4)}`;
}

/** `11%` of a lane's context window, or null when nobody reported one. */
export function contextUsedPct(
  tokens: number | null | undefined,
  window: number | null | undefined,
): number | null {
  if (typeof tokens !== 'number' || typeof window !== 'number' || window <= 0) return null;
  return clamp(Math.round((tokens / window) * 100), 0, 100);
}
