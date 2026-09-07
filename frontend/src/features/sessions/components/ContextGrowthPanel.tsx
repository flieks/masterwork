import { useAtom } from 'jotai';
import { AlertTriangle } from 'lucide-react';
import type { ContextSample, ContextToolCost } from '~/api/generated';
import { apiErrorMessage } from '~/api/client';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { formatTokens } from '~/lib/timeline';
import { sessionContextQueryAtom } from '../queries';
import { ToolChip } from './ToolChip';

/** Percentage-space viewBox, like every other chart in this feature — no
 * charting dependency exists in this repo and none is added for this one. */
const CHART_WIDTH = 100;
const CHART_HEIGHT = 40;

/** The same red RunWaterfall reserves for an alarming phase. */
const TRUNCATION_COLOR = 'hsl(0 72% 51%)';

interface ContextGrowthPanelProps {
  sessionId: string;
  /** Switches the caller's tab to All events and filters the stream to this tool. */
  onSelectTool: (tool: string) => void;
}

/**
 * How the context window filled up over the run, and which tool grew it.
 *
 * Every session recorded before this shipped carries no samples, so an empty
 * series renders nothing rather than an empty box on hundreds of old runs.
 */
export function ContextGrowthPanel({ sessionId, onSelectTool }: ContextGrowthPanelProps) {
  const [{ data, isPending, isError, error, refetch }] = useAtom(
    sessionContextQueryAtom(sessionId),
  );

  if (isPending) {
    return (
      <div className="flex flex-col gap-3 rounded-lg border bg-card p-4 sm:flex-row">
        <Skeleton className="h-40 flex-1" />
        <Skeleton className="h-40 w-48 shrink-0" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
        <p className="flex items-center gap-2">
          <AlertTriangle className="size-4" /> Couldn't load the context series.
        </p>
        <p>{apiErrorMessage(error)}</p>
        <Button type="button" variant="outline" size="sm" onClick={() => void refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  if (!data || data.samples.length === 0) return null;

  return (
    <section
      aria-label="Context growth"
      className="flex flex-col gap-4 rounded-lg border bg-card p-4 sm:flex-row"
    >
      <div className="min-w-0 flex-1">
        <div className="mb-2 flex items-baseline justify-between">
          <h3 className="text-[11px] uppercase tracking-wide text-muted-foreground">
            Context window
          </h3>
          <span className="font-mono text-[11px] text-muted-foreground">
            {formatTokens(data.baseline_tokens)} → {formatTokens(data.peak_tokens)}
          </span>
        </div>
        <ContextChart samples={data.samples} />
        <TruncationNote samples={data.samples} />
      </div>

      {data.tools.length > 0 ? (
        <div className="w-full shrink-0 sm:w-56">
          <h3 className="mb-2 text-[11px] uppercase tracking-wide text-muted-foreground">
            Grew it
          </h3>
          <ToolRollup tools={data.tools} onSelectTool={onSelectTool} />
        </div>
      ) : null}
    </section>
  );
}

/** An area + line of `total_tokens` over `seq`, with truncation drops marked. */
function ContextChart({ samples }: { samples: ContextSample[] }) {
  const maxTokens = Math.max(...samples.map((s) => s.total_tokens), 1);
  const stepX = samples.length > 1 ? CHART_WIDTH / (samples.length - 1) : 0;
  const points = samples.map((sample, i) => ({
    x: samples.length > 1 ? i * stepX : CHART_WIDTH / 2,
    y: CHART_HEIGHT - (sample.total_tokens / maxTokens) * CHART_HEIGHT,
    sample,
  }));
  const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ');
  const first = points[0];
  const last = points[points.length - 1];
  const areaPath = first && last ? `${linePath} L ${last.x} ${CHART_HEIGHT} L ${first.x} ${CHART_HEIGHT} Z` : '';

  return (
    <svg
      viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
      preserveAspectRatio="none"
      role="img"
      aria-label="Total context tokens over the run"
      className="h-40 w-full overflow-visible"
    >
      <path d={areaPath} fill="hsl(var(--primary) / 0.12)" stroke="none" />
      <path
        d={linePath}
        fill="none"
        stroke="hsl(var(--primary))"
        strokeWidth={1}
        vectorEffect="non-scaling-stroke"
      />
      {points.map((p) =>
        p.sample.is_truncation ? (
          <g key={p.sample.seq} data-context-point={p.sample.seq} data-truncation="true">
            <line
              x1={p.x}
              x2={p.x}
              y1={0}
              y2={CHART_HEIGHT}
              stroke={TRUNCATION_COLOR}
              strokeOpacity={0.5}
              strokeDasharray="1.5 1.5"
              vectorEffect="non-scaling-stroke"
            />
            <circle cx={p.x} cy={p.y} r={1.4} fill={TRUNCATION_COLOR}>
              <title>{`Turn ${p.sample.seq} — context truncated, ${formatTokens(Math.abs(p.sample.delta_tokens ?? 0))} tokens dropped`}</title>
            </circle>
          </g>
        ) : (
          <circle
            key={p.sample.seq}
            data-context-point={p.sample.seq}
            cx={p.x}
            cy={p.y}
            r={0.7}
            fill="hsl(var(--primary))"
          >
            <title>{`Turn ${p.sample.seq} — ${formatTokens(p.sample.total_tokens)} tokens`}</title>
          </circle>
        ),
      )}
    </svg>
  );
}

/** Named in a caption, not just a tooltip — a truncation is worth reading even
 * without hovering the exact point. */
function TruncationNote({ samples }: { samples: ContextSample[] }) {
  const truncations = samples.filter((s) => s.is_truncation);
  if (truncations.length === 0) return null;
  const largest = Math.max(...truncations.map((s) => Math.abs(s.delta_tokens ?? 0)));
  return (
    <p className="mt-1.5 font-mono text-[10px] text-amber-700 dark:text-amber-500">
      {truncations.length} context truncation{truncations.length === 1 ? '' : 's'} — largest drop{' '}
      {formatTokens(largest)} tokens
    </p>
  );
}

/** The per-tool roll-up, ranked highest-growth first — each row filters the timeline. */
function ToolRollup({
  tools,
  onSelectTool,
}: {
  tools: ContextToolCost[];
  onSelectTool: (tool: string) => void;
}) {
  const total = tools.reduce((sum, t) => sum + t.delta_tokens, 0) || 1;
  return (
    <ol className="flex flex-col gap-1.5">
      {tools.map((row) => (
        <li key={row.tool}>
          <button
            type="button"
            onClick={() => onSelectTool(row.tool)}
            className="flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-xs transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ToolChip toolName={row.tool} className="shrink-0" />
            <span className="ml-auto shrink-0 font-mono tabular-nums text-muted-foreground">
              +{formatTokens(row.delta_tokens)}
            </span>
            <span className="w-7 shrink-0 text-right font-mono text-[10px] text-muted-foreground">
              ×{row.calls}
            </span>
          </button>
          <div className="mx-1.5 mt-0.5 h-1 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary/60"
              style={{ width: `${Math.round((row.delta_tokens / total) * 100)}%` }}
            />
          </div>
        </li>
      ))}
    </ol>
  );
}
