import type { GateStat, ModelStat, RunStat } from '~/api/generated';

/**
 * How the cross-run aggregates read. Four endpoints hand back rates, denominators
 * and a deliberate null; this module is the single place that decides what each
 * of those means on screen, so no table can quietly disagree with another.
 *
 * A leaf module — no atoms, no API client — so a spec can import the vocabulary
 * without `import.meta.env` (which the client reads) having to exist.
 */

/** What an undefined rate renders as. Never "0%": nothing was measured. */
export const RATE_UNKNOWN = '—';

export const RATE_UNKNOWN_HINT =
  'Unknown, not zero: nothing was measured, so there is no rate to compute. The API sends null rather than 0 so this cannot read as "never fails".';

/**
 * Below this many observations a rate is noise and is shown muted. It is a
 * display decision and lives here rather than in the API on purpose — a
 * server-side minimum would delete the only rows a small dataset has, so every
 * row is still shown, and its denominator is always next to it.
 */
const SMALL_SAMPLE = 5;

export function isSmallSample(denominator: number): boolean {
  return denominator > 0 && denominator < SMALL_SAMPLE;
}

/** "18%" — a rate as a whole percent, or the unknown mark. */
export function formatRate(rate: number | null | undefined): string {
  if (rate === null || rate === undefined) return RATE_UNKNOWN;
  return `${Math.round(rate * 100)}%`;
}

/** "17 checks" / "1 check" — the denominator, spelled out beside its rate. */
export function denominatorLabel(count: number, noun: string): string {
  return `${count} ${count === 1 ? noun : `${noun}s`}`;
}

/**
 * The `model: null` row is not a model. It is every lane that named none —
 * including the pipeline's own `git` and `checks` lanes, which run no model and
 * appear in every run — so a run is counted here *and* under its real model,
 * and its acceptance rate sits near the whole population's by construction.
 */
const UNATTRIBUTED = 'unattributed';

export const UNATTRIBUTED_MODEL_HINT =
  'Not a model: every lane that named none. The pipeline’s own git and checks lanes run no model and appear in every run, so runs counted here are also counted under their real model — its acceptance rate is the whole population’s, not a verdict on anything.';

export const UNATTRIBUTED_ROLE_HINT = 'A stage that reported no role.';

/** A model name for display; the unnamed row is labelled, never left blank. */
export function modelLabel(model: string | null): string {
  return model ?? UNATTRIBUTED;
}

export function isUnattributedModel(row: ModelStat): boolean {
  return row.model === null;
}

/** A role name for display; a stage that named none reads the same way. */
export function roleLabel(role: string | null): string {
  return role ?? UNATTRIBUTED;
}

/** "$1.42" / "$0.0518" — small costs need the extra places to stay a number. */
export function formatUsd(usd: number | null | undefined): string {
  if (usd === null || usd === undefined) return RATE_UNKNOWN;
  if (usd === 0) return '$0';
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}

/** "1.2M" / "48.3k" / "912" — token counts at a glance. */
export function formatTokens(tokens: number | null | undefined): string {
  if (tokens === null || tokens === undefined) return RATE_UNKNOWN;
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}k`;
  return `${tokens}`;
}

/** "49s" / "6m 13s" / "1h 2m" — a duration in ms, or the unknown mark. */
export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return RATE_UNKNOWN;
  const total = Math.round(ms / 1000);
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) {
    const rest = total % 60;
    return rest === 0 ? `${minutes}m` : `${minutes}m ${rest}s`;
  }
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest === 0 ? `${hours}h` : `${hours}h ${rest}m`;
}

/** "0.58" — an average that is not a rate, or the unknown mark. */
export function formatAverage(value: number | null | undefined): string {
  if (value === null || value === undefined) return RATE_UNKNOWN;
  return value.toFixed(2);
}

/** How far back every aggregate looks. Shared, so the four share a population. */
export type AnalyticsWindow = '24h' | '7d' | '30d' | 'all';

const WINDOW_HOURS: Record<Exclude<AnalyticsWindow, 'all'>, number> = {
  '24h': 24,
  '7d': 24 * 7,
  '30d': 24 * 30,
};

/** The `since` bound for a window, or undefined for all time. */
export function analyticsSince(window: AnalyticsWindow, now = Date.now()): string | undefined {
  if (window === 'all') return undefined;
  return new Date(now - WINDOW_HOURS[window] * 3_600_000).toISOString();
}

/** Bar width as a percentage of the widest row, floored so a 1 stays visible. */
export function barPct(value: number, max: number): number {
  if (max <= 0 || value <= 0) return 0;
  return Math.max(2, Math.min(100, Math.round((value / max) * 100)));
}

/** The gates worth reading first: the ones that actually failed. */
export function failingGates(rows: readonly GateStat[]): GateStat[] {
  return rows.filter((row) => row.failures > 0);
}

/** Which figure the run trend plots. Each can be absent on a given run. */
export type RunMetric = 'cost' | 'tokens' | 'duration';

export const RUN_METRIC_LABEL: Record<RunMetric, string> = {
  cost: 'Cost',
  tokens: 'Tokens',
  duration: 'Duration',
};

/**
 * The plotted figure for one run, or null when the run never reported it — a
 * missing cost is not a free run, and a zero-height bar would say it was.
 */
export function runMetricValue(run: RunStat, metric: RunMetric): number | null {
  if (metric === 'cost') return run.cost_usd;
  if (metric === 'tokens') return run.tokens_total;
  return run.active_ms;
}

export function formatRunMetric(value: number | null, metric: RunMetric): string {
  if (value === null) return RATE_UNKNOWN;
  if (metric === 'cost') return formatUsd(value);
  if (metric === 'tokens') return formatTokens(value);
  return formatMs(value);
}

/** The tallest reported value in the series; 0 when nothing reported one. */
export function runMetricMax(runs: readonly RunStat[], metric: RunMetric): number {
  let max = 0;
  for (const run of runs) {
    const value = runMetricValue(run, metric);
    if (value !== null && value > max) max = value;
  }
  return max;
}

/** How many runs in the series never reported the plotted figure. */
export function runsMissingMetric(runs: readonly RunStat[], metric: RunMetric): number {
  return runs.filter((run) => runMetricValue(run, metric) === null).length;
}
