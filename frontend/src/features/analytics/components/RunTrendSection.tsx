import { useState } from 'react';
import { useAtom } from 'jotai';
import { Link } from 'react-router-dom';
import { Activity } from 'lucide-react';
import type { RunStat } from '~/api/generated';
import { EmptyState } from '~/components/EmptyState';
import { absoluteDateTime } from '~/lib/datetime';
import { cn } from '~/lib/utils';
import { sessionDetailPath } from '~/features/sessions';
import { runStatsQueryAtom } from '../queries';
import {
  RATE_UNKNOWN,
  barPct,
  denominatorLabel,
  formatRate,
  formatRunMetric,
  formatUsd,
  runMetricMax,
  runMetricValue,
  runsMissingMetric,
  RUN_METRIC_LABEL,
  type RunMetric,
} from '../stats';
import { SectionShell } from './SectionShell';

/**
 * Is it getting worse.
 *
 * One bar per run, oldest on the left, because that is the direction a
 * regression is read in. Drawn with CSS boxes rather than a charting library —
 * the run cards and the usage rollup already draw their bars this way, and a
 * hundred divs is cheaper than a dependency.
 */
export function RunTrendSection() {
  const [{ data, isPending, isError, error, refetch }] = useAtom(runStatsQueryAtom);
  // Which figure is plotted is a local view preference: nothing else reads it,
  // it does not belong in the URL, and Radix unmounts the panel anyway.
  //
  // Duration leads because it is the only one of the three every run reports —
  // cost and tokens are absent on all but the pipeline runs, so opening on cost
  // would show a mostly empty chart and read as a quiet history.
  const [metric, setMetric] = useState<RunMetric>('duration');

  return (
    <SectionShell
      title="Runs over time"
      description="One point per run, oldest first. The most recent runs the filters allow, so a window that ends today ends at the right-hand edge."
      count={data ? denominatorLabel(data.length, 'run') : undefined}
      isPending={isPending}
      isError={isError}
      error={error}
      onRetry={refetch}
      empty={
        data?.length === 0 ? (
          <EmptyState
            icon={<Activity className="size-8" />}
            title="No run in this window"
            description="Every recorded run is a point here. Widen the window, or clear the workflow filter — and note that runs another run launched are folded into their parent unless you include them."
          />
        ) : null
      }
    >
      {data && data.length > 0 ? (
        <div className="flex min-w-0 flex-col gap-3">
          <TrendSummary runs={data} metric={metric} onMetricChange={setMetric} />
          <TrendChart runs={data} metric={metric} />
        </div>
      ) : null}
    </SectionShell>
  );
}

function TrendSummary({
  runs,
  metric,
  onMetricChange,
}: {
  runs: RunStat[];
  metric: RunMetric;
  onMetricChange: (metric: RunMetric) => void;
}) {
  const accepted = runs.filter((run) => run.accepted).length;
  const cost = runs.reduce((sum, run) => sum + (run.cost_usd ?? 0), 0);
  const missing = runsMissingMetric(runs, metric);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>
          <span className="font-mono text-sm tabular-nums text-foreground">
            {formatRate(runs.length > 0 ? accepted / runs.length : null)}
          </span>{' '}
          accepted ({accepted} of {runs.length})
        </span>
        <span>
          <span className="font-mono text-sm tabular-nums text-foreground">{formatUsd(cost)}</span>{' '}
          reported in total
        </span>
        {missing > 0 ? (
          // Not a footnote: on a chat-heavy population most of the bars are the
          // absence of a figure, and a chart that did not say so would read as
          // a run that cost nothing.
          <span>
            {denominatorLabel(missing, 'run')} reported no {RUN_METRIC_LABEL[metric].toLowerCase()}{' '}
            — drawn as {RATE_UNKNOWN}
          </span>
        ) : null}
      </div>

      <div className="inline-flex items-center gap-1.5">
        <span className="text-[11px] uppercase tracking-wide text-muted-foreground">Plot</span>
        <div className="inline-flex rounded-md border p-0.5" role="group" aria-label="Plot">
          {(['cost', 'tokens', 'duration'] as const).map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={metric === option}
              onClick={() => onMetricChange(option)}
              className={cn(
                'rounded-sm px-2 py-1 text-xs transition-colors',
                metric === option
                  ? 'bg-secondary font-medium text-secondary-foreground'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {RUN_METRIC_LABEL[option]}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function TrendChart({ runs, metric }: { runs: RunStat[]; metric: RunMetric }) {
  const max = runMetricMax(runs, metric);
  const first = runs[0];
  const last = runs[runs.length - 1];

  return (
    <div className="flex flex-col gap-1.5 rounded-lg border p-3">
      {/*
        Each bar holds a clickable minimum width, so a long series cannot be
        squeezed indefinitely. Past that point the strip scrolls inside its own
        box rather than widening the panel and scrolling the whole page.
      */}
      <div
        className="flex h-32 min-w-0 items-end gap-px overflow-x-auto border-b"
        role="list"
        aria-label="Runs over time"
      >
        {runs.map((run) => (
          <RunBar key={run.session_id} run={run} metric={metric} max={max} />
        ))}
      </div>
      <div className="flex items-center justify-between font-mono text-[10px] text-muted-foreground">
        <span>{absoluteDateTime(first.started_at)}</span>
        <span>{absoluteDateTime(last.started_at)}</span>
      </div>
      <Legend hasUnknown={runsMissingMetric(runs, metric) > 0} />
    </div>
  );
}

function RunBar({ run, metric, max }: { run: RunStat; metric: RunMetric; max: number }) {
  const value = runMetricValue(run, metric);
  const unknown = value === null;
  const height = unknown ? 0 : barPct(value, max);

  const title = [
    run.title ?? run.session_id,
    `${run.status}${run.workflow ? ` · ${run.workflow}` : ''}`,
    `${RUN_METRIC_LABEL[metric]}: ${formatRunMetric(value, metric)}`,
    `${run.stages} stages, ${run.corrections} corrections`,
    // Both gate populations side by side, because they can legitimately differ.
    `Gates: ${run.gates_failed} failed of the stage counters, ${run.gate_failures} of ${run.gate_checks} evidence checks`,
    absoluteDateTime(run.started_at),
  ].join('\n');

  return (
    <Link
      to={sessionDetailPath(run.session_id)}
      role="listitem"
      title={title}
      aria-label={`${run.title ?? run.session_id} — ${run.status}, ${RUN_METRIC_LABEL[metric]} ${formatRunMetric(value, metric)}`}
      className="group relative flex h-full min-w-[3px] flex-1 items-end"
    >
      {unknown ? (
        // Never a zero-height bar: the run did not report this figure, and a
        // flat bar at the baseline would claim it reported nought.
        <span
          data-run-bar="unknown"
          className="block h-px w-full border-t border-dashed border-muted-foreground/60 group-hover:border-foreground"
        />
      ) : (
        <span
          data-run-bar={run.accepted ? 'accepted' : 'not-accepted'}
          className={cn(
            'block w-full rounded-t-[1px] transition-colors',
            run.accepted
              ? 'bg-primary/45 group-hover:bg-primary/80'
              : 'bg-destructive/50 group-hover:bg-destructive/80',
          )}
          // A reported zero still gets a solid hairline: it means the run took
          // no measurable time, which is a different statement from the dashed
          // tick that means the run never said.
          style={{ height: `${height}%`, minHeight: '1px' }}
        />
      )}
    </Link>
  );
}

function Legend({ hasUnknown }: { hasUnknown: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
      <span className="inline-flex items-center gap-1.5">
        <span className="size-2 rounded-[1px] bg-primary/45" aria-hidden="true" />
        accepted
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="size-2 rounded-[1px] bg-destructive/50" aria-hidden="true" />
        not accepted — a run that failed, or fell silent before it said
      </span>
      {/* Only when there is one to point at: a key to a mark the chart is not
          drawing would imply the data has gaps it does not have. */}
      {hasUnknown ? (
        <span className="inline-flex items-center gap-1.5">
          <span
            className="w-3 border-t border-dashed border-muted-foreground/60"
            aria-hidden="true"
          />
          never reported this figure
        </span>
      ) : null}
    </div>
  );
}
